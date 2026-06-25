"""OpenColorIO and OpenImageIO-backed color transforms for OpenComp.

This engine loads the active OCIO config, exposes viewer-facing config metadata,
and runs CPU or OIIO-assisted color transforms for graph evaluation and viewer
display conversion. Optional integrations stay observable through diagnostics.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np

from opencomp.core.optional_dependencies import import_first_available, import_optional


class OCIOUnavailableError(RuntimeError):
    pass


@dataclass
class OCIOColorEngine:
    config_path_or_builtin: str | None = None
    _ocio: Any | None = field(default=None, init=False, repr=False)
    _oiio: Any | None = field(default=None, init=False, repr=False)
    _ocio_module_name: str | None = field(default=None, init=False, repr=False)
    _oiio_module_name: str | None = field(default=None, init=False, repr=False)
    _config: Any | None = field(default=None, init=False, repr=False)
    _processor_cache: dict[tuple[str, ...], Any] = field(default_factory=dict, init=False, repr=False)
    _oiio_config_path: str | None = field(default=None, init=False, repr=False)
    _oiio_temp_config_path: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        ocio_dependency = import_first_available("PyOpenColorIO", "opencolorio")
        self._ocio = ocio_dependency.module
        self._ocio_module_name = ocio_dependency.module_name
        self._oiio = import_optional("OpenImageIO")
        self._oiio_module_name = "OpenImageIO" if self._oiio is not None else None
        if self._ocio is None:
            return
        self._config = self._load_config(self.config_path_or_builtin)

    def __del__(self) -> None:  # pragma: no cover - best-effort cleanup
        try:
            self.close()
        except Exception:
            return

    def close(self) -> None:
        if self._oiio_temp_config_path:
            try:
                Path(self._oiio_temp_config_path).unlink()
            except OSError:
                pass
            self._oiio_temp_config_path = None

    @property
    def available(self) -> bool:
        return self._ocio is not None and self._config is not None

    def diagnostics(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "ocio_available": self._ocio is not None,
            "oiio_available": self._oiio is not None,
            "ocio_module": self._ocio_module_name,
            "oiio_module": self._oiio_module_name,
            "config_loaded": self._config is not None,
            "config_source": self.config_path_or_builtin,
            "warning": self._diagnostics_warning(),
        }

    def _load_config(self, config_path_or_builtin: str | None) -> Any:
        ocio = self._ocio
        assert ocio is not None

        if config_path_or_builtin:
            if config_path_or_builtin.endswith(".ocio"):
                return ocio.Config.CreateFromFile(config_path_or_builtin)
            if hasattr(ocio, "BuiltinConfigRegistry"):
                return ocio.Config.CreateFromBuiltinConfig(config_path_or_builtin)

        if hasattr(ocio, "BuiltinConfigRegistry"):
            registry = ocio.BuiltinConfigRegistry()
            builtin_names = [item[0] for item in registry.getBuiltinConfigs()]
            for preferred in (
                "studio-config-v4.0.0_aces-v2.0_ocio-v2.5",
                "cg-config-v4.0.0_aces-v2.0_ocio-v2.5",
                "studio-config-v2.2.0_aces-v1.3_ocio-v2.4",
                "cg-config-v2.2.0_aces-v1.3_ocio-v2.4",
            ):
                if preferred in builtin_names:
                    return ocio.Config.CreateFromBuiltinConfig(preferred)

        return ocio.GetCurrentConfig()

    def colorspaces(self) -> list[str]:
        if not self.available:
            return ["ACES2065-1", "ACEScg", "sRGB - Texture", "Output - sRGB"]
        if hasattr(self._config, "getColorSpaceNames"):
            return list(self._config.getColorSpaceNames())
        return [self._config.getColorSpaceNameByIndex(i) for i in range(self._config.getNumColorSpaces())]

    def builtin_configs(self) -> list[dict[str, Any]]:
        if self._ocio is None or not hasattr(self._ocio, "BuiltinConfigRegistry"):
            return []
        registry = self._ocio.BuiltinConfigRegistry()
        return [
            {
                "name": item[0],
                "description": item[1],
                "is_default": bool(item[2]) if len(item) > 2 else False,
                "is_recommended": bool(item[3]) if len(item) > 3 else False,
            }
            for item in registry.getBuiltinConfigs()
        ]

    def displays(self) -> list[str]:
        if not self.available:
            return ["sRGB"]
        if hasattr(self._config, "getDisplays"):
            return list(self._config.getDisplays())
        return [self._config.getDisplay(i) for i in range(self._config.getNumDisplays())]

    def views(self, display: str | None = None) -> list[str]:
        if not self.available:
            return ["ACES 1.0 - SDR Video", "Raw"]
        display_name = display or self._default_display()
        if hasattr(self._config, "getViews"):
            return list(self._config.getViews(display_name))
        return [self._config.getView(display_name, i) for i in range(self._config.getNumViews(display_name))]

    def default_display(self) -> str | None:
        if not self.available:
            return "sRGB"
        return self._default_display()

    def default_view(self, display: str | None = None) -> str | None:
        if not self.available:
            return "ACES 1.0 - SDR Video"
        return self._default_view(display or self._default_display())

    def convert_colorspace(self, rgba: np.ndarray, src: str, dst: str) -> np.ndarray:
        image = self._ensure_rgba(rgba)
        if src == dst:
            return image.copy()
        if not self.available:
            raise OCIOUnavailableError(
                "OpenColorIO Python bindings are not installed; cannot convert colorspaces."
            )
        cpu = self._get_colorspace_processor(src, dst)
        return self._apply_with_optional_oiio(
            image,
            lambda: self._apply_oiio_colorconvert(image, src, dst),
            lambda: self._apply_cpu_processor(cpu, image),
        )

    def apply_display_view(
        self,
        rgba: np.ndarray,
        src: str,
        display: str | None = None,
        view: str | None = None,
    ) -> np.ndarray:
        image = self._ensure_rgba(rgba)
        if not self.available:
            return image.copy()
        display_name = display or self._default_display()
        view_name = view or self._default_view(display_name)
        cpu = self._get_display_processor(src, display_name, view_name)
        return self._apply_with_optional_oiio(
            image,
            lambda: self._apply_oiio_display(image, src, display_name, view_name),
            lambda: self._apply_cpu_processor(cpu, image),
        )

    def gpu_display_shader(
        self,
        src: str,
        display: str | None = None,
        view: str | None = None,
    ) -> dict[str, Any]:
        if not self.available:
            return {
                "available": False,
                "reason": "OpenColorIO Python bindings or an OCIO config are not available.",
                "source": src,
                "display": display,
                "view": view,
                "language": "GLSL",
                "shader_text": None,
                "function_name": None,
                "textures": [],
            }

        display_name = display or self._default_display()
        view_name = view or self._default_view(display_name)
        try:
            ocio = self._ocio
            assert ocio is not None
            processor = self._get_display_processor_object(src, display_name, view_name).getDefaultGPUProcessor()
            shader_desc = self._create_gpu_shader_desc()
            processor.extractGpuShaderInfo(shader_desc)
            shader_text = shader_desc.getShaderText() if hasattr(shader_desc, "getShaderText") else ""
            function_name = shader_desc.getFunctionName() if hasattr(shader_desc, "getFunctionName") else "OCIODisplay"
            return {
                "available": bool(shader_text),
                "reason": None if shader_text else "OCIO returned an empty GPU shader.",
                "source": src,
                "display": display_name,
                "view": view_name,
                "language": "GLSL",
                "shader_text": shader_text,
                "function_name": function_name,
                "resource_prefix": "ocio_",
                "requires_lut_textures": bool(self._gpu_textures(shader_desc)),
                "textures": self._gpu_textures(shader_desc),
            }
        except Exception as exc:
            return {
                "available": False,
                "reason": str(exc),
                "source": src,
                "display": display_name,
                "view": view_name,
                "language": "GLSL",
                "shader_text": None,
                "function_name": None,
                "textures": [],
            }

    def _ensure_rgba(self, rgba: np.ndarray) -> np.ndarray:
        image = np.asarray(rgba, dtype=np.float32)
        if image.ndim != 3 or image.shape[2] != 4:
            raise ValueError("OCIO operations require an H x W x 4 float32 RGBA array.")
        return np.ascontiguousarray(image)

    def _oiio_colorconfig_path(self) -> str:
        if self._oiio_config_path:
            return self._oiio_config_path
        if self.config_path_or_builtin and self.config_path_or_builtin.endswith(".ocio"):
            self._oiio_config_path = self.config_path_or_builtin
            return self._oiio_config_path
        if self._config is None or not hasattr(self._config, "serialize"):
            raise RuntimeError("Current OCIO config cannot be materialized for OpenImageIO.")
        with tempfile.NamedTemporaryFile("w", suffix=".ocio", delete=False, encoding="utf-8") as handle:
            handle.write(self._config.serialize())
            self._oiio_temp_config_path = handle.name
        self._oiio_config_path = self._oiio_temp_config_path
        return self._oiio_config_path

    def _oiio_imagebuf_from_rgba(self, image: np.ndarray) -> Any:
        oiio = self._oiio
        if oiio is None:
            raise RuntimeError("OpenImageIO is not available.")
        height, width = image.shape[:2]
        spec = oiio.ImageSpec(width, height, 4, oiio.FLOAT)
        spec.channelnames = ("R", "G", "B", "A")
        buf = oiio.ImageBuf(spec)
        roi = oiio.ROI(0, width, 0, height, 0, 1, 0, 4)
        if not buf.set_pixels(roi, np.ascontiguousarray(image, dtype=np.float32)):
            raise RuntimeError(buf.geterror() if hasattr(buf, "geterror") else "OpenImageIO could not upload pixels.")
        return buf

    def _oiio_imagebuf_to_rgba(self, buf: Any) -> np.ndarray:
        oiio = self._oiio
        if oiio is None:
            raise RuntimeError("OpenImageIO is not available.")
        pixels = buf.get_pixels(oiio.FLOAT)
        image = np.ascontiguousarray(np.asarray(pixels, dtype=np.float32))
        if image.ndim != 3:
            raise RuntimeError(f"Unexpected OpenImageIO output layout {image.shape!r}.")
        if image.shape[2] == 4:
            return image
        if image.shape[2] == 3:
            rgba = np.empty((image.shape[0], image.shape[1], 4), dtype=np.float32)
            rgba[:, :, :3] = image
            rgba[:, :, 3] = 1.0
            return rgba
        raise RuntimeError(f"Unexpected OpenImageIO channel count {image.shape[2]}.")

    def _apply_oiio_colorconvert(self, image: np.ndarray, src: str, dst: str) -> np.ndarray:
        oiio = self._oiio
        if oiio is None:
            raise RuntimeError("OpenImageIO is not available.")
        buf = self._oiio_imagebuf_from_rgba(image)
        converted = oiio.ImageBufAlgo.colorconvert(
            buf,
            src,
            dst,
            True,
            "",
            "",
            self._oiio_colorconfig_path(),
        )
        if hasattr(converted, "has_error") and converted.has_error:
            raise RuntimeError(converted.geterror())
        return self._oiio_imagebuf_to_rgba(converted)

    def _apply_oiio_display(self, image: np.ndarray, src: str, display: str, view: str) -> np.ndarray:
        oiio = self._oiio
        if oiio is None:
            raise RuntimeError("OpenImageIO is not available.")
        buf = self._oiio_imagebuf_from_rgba(image)
        displayed = oiio.ImageBufAlgo.ociodisplay(
            buf,
            display,
            view,
            src,
            "",
            True,
            False,
            "",
            "",
            self._oiio_colorconfig_path(),
        )
        if hasattr(displayed, "has_error") and displayed.has_error:
            raise RuntimeError(displayed.geterror())
        return self._oiio_imagebuf_to_rgba(displayed)

    def _apply_with_optional_oiio(
        self,
        image: np.ndarray,
        oiio_transform: Callable[[], np.ndarray],
        fallback_transform: Callable[[], np.ndarray],
    ) -> np.ndarray:
        if self._oiio is not None:
            try:
                return oiio_transform()
            except Exception:
                pass
        return fallback_transform()

    def _diagnostics_warning(self) -> str | None:
        if self._ocio is None:
            return "OpenColorIO Python bindings are unavailable. Color conversion falls back to pass-through behavior where supported."
        if self._config is None:
            return "OpenColorIO is installed, but no config could be loaded."
        if self._oiio is None:
            return "OpenImageIO is unavailable. OCIO CPU processors remain available, but OIIO-accelerated color conversions are disabled."
        return None

    def _get_colorspace_processor(self, src: str, dst: str) -> Any:
        key = ("colorspace", src, dst)
        if key not in self._processor_cache:
            processor = self._config.getProcessor(src, dst)
            self._processor_cache[key] = processor.getDefaultCPUProcessor()
        return self._processor_cache[key]

    def _get_display_processor(self, src: str, display: str, view: str) -> Any:
        key = ("display_cpu", src, display, view)
        if key in self._processor_cache:
            return self._processor_cache[key]

        processor = self._get_display_processor_object(src, display, view)
        self._processor_cache[key] = processor.getDefaultCPUProcessor()
        return self._processor_cache[key]

    def _get_display_processor_object(self, src: str, display: str, view: str) -> Any:
        key = ("display_processor", src, display, view)
        if key in self._processor_cache:
            return self._processor_cache[key]

        ocio = self._ocio
        assert ocio is not None
        try:
            transform = ocio.DisplayViewTransform()
            transform.setSrc(src)
            transform.setDisplay(display)
            transform.setView(view)
            processor = self._config.getProcessor(transform)
        except Exception:
            direction = getattr(ocio, "TRANSFORM_DIR_FORWARD", None)
            processor = self._config.getProcessor(src, display, view, direction)
        self._processor_cache[key] = processor
        return self._processor_cache[key]

    def _create_gpu_shader_desc(self) -> Any:
        ocio = self._ocio
        assert ocio is not None
        if hasattr(ocio, "GpuShaderDesc") and hasattr(ocio.GpuShaderDesc, "CreateShaderDesc"):
            shader_desc = ocio.GpuShaderDesc.CreateShaderDesc()
        else:
            shader_desc = ocio.GpuShaderDesc()
        if hasattr(shader_desc, "setFunctionName"):
            shader_desc.setFunctionName("OCIODisplay")
        if hasattr(shader_desc, "setResourcePrefix"):
            shader_desc.setResourcePrefix("ocio_")
        if hasattr(shader_desc, "setLanguage"):
            for language_name in ("GPU_LANGUAGE_GLSL_4_0", "GPU_LANGUAGE_GLSL_1_3", "GPU_LANGUAGE_GLSL"):
                language = getattr(ocio, language_name, None)
                if language is None:
                    continue
                try:
                    shader_desc.setLanguage(language)
                    break
                except Exception:
                    continue
        return shader_desc

    def _gpu_textures(self, shader_desc: Any) -> list[dict[str, Any]]:
        textures: list[dict[str, Any]] = []
        for getter_name in ("getTextures", "get3DTextures"):
            if not hasattr(shader_desc, getter_name):
                continue
            try:
                iterator = getattr(shader_desc, getter_name)()
            except Exception:
                continue
            for texture in iterator:
                try:
                    values = texture.getValues()
                except Exception:
                    values = []
                texture_type = str(getattr(texture, "dimensions", ""))
                textures.append(
                    {
                        "texture_name": str(getattr(texture, "textureName", "")),
                        "sampler_name": str(getattr(texture, "samplerName", "")),
                        "binding": int(getattr(texture, "textureShaderBindingIndex", len(textures) + 1)),
                        "width": int(getattr(texture, "width", 1) or 1),
                        "height": int(getattr(texture, "height", 1) or 1),
                        "channels": str(getattr(texture, "channel", "")),
                        "dimensions": texture_type,
                        "interpolation": str(getattr(texture, "interpolation", "")),
                        "values": np.asarray(values, dtype=np.float32).reshape(-1).tolist(),
                    }
                )
        return textures

    def _apply_cpu_processor(self, cpu: Any, image: np.ndarray) -> np.ndarray:
        result = image.copy()
        flat = np.ascontiguousarray(result.reshape((-1, 4)))
        cpu.applyRGBA(flat)
        return flat.reshape(result.shape)

    def _default_display(self) -> str:
        try:
            return self._config.getDefaultDisplay()
        except Exception:
            return self._config.getDisplay(0)

    def _default_view(self, display: str) -> str:
        try:
            return self._config.getDefaultView(display)
        except Exception:
            return self._config.getView(display, 0)
