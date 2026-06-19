import type { FloatViewerFrame, FloatViewerPixels, OcioGpuShader, OcioGpuTexture } from "../api/client";

export type WebglViewerCompareMode = "none" | "wipe" | "difference";

export type WebglViewerMetrics = {
  mode: "webgl-float";
  ocio_gpu: boolean;
  fallback_reason: string | null;
  upload_ms: number;
  draw_ms: number;
  compile_ms: number;
  width: number;
  height: number;
  bytes: number;
  timestamp: number;
};

export type WebglViewerRenderOptions = {
  frameA: FloatViewerFrame;
  frameB?: FloatViewerFrame | null;
  ocioShader?: OcioGpuShader | null;
  viewerProcess: {
    gain: number;
    saturation: number;
    fstop: number;
  };
  compareMode: WebglViewerCompareMode;
  wipePosition: number;
  wipeAngle: number;
  transform: {
    x: number;
    y: number;
    scale: number;
  };
  canvasCssSize: {
    width: number;
    height: number;
  };
  pixelRatio: number;
  pixelAspect: number;
};

const VERTEX_SHADER = `#version 300 es
in vec2 a_position;

void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`;

export function isWebglFloatViewerSupported(): boolean {
  const canvas = document.createElement("canvas");
  return Boolean(canvas.getContext("webgl2"));
}

export class WebglFloatViewerRenderer {
  private gl: WebGL2RenderingContext;
  private quadBuffer: WebGLBuffer;
  private program: WebGLProgram | null = null;
  private programKey = "";
  private programUsesOcio = false;
  private fallbackReason: string | null = null;
  private lastCompileMs = 0;
  private frameATexture: WebGLTexture | null = null;
  private frameBTexture: WebGLTexture | null = null;
  private frameAData: FloatViewerPixels | null = null;
  private frameBData: FloatViewerPixels | null = null;
  private frameARevision = -1;
  private frameBRevision = -1;
  private ocioTextureKey = "";
  private ocioTextures = new Map<string, WebGLTexture>();

  constructor(private canvas: HTMLCanvasElement) {
    const gl = canvas.getContext("webgl2", {
      alpha: false,
      antialias: false,
      depth: false,
      stencil: false,
      premultipliedAlpha: false,
      preserveDrawingBuffer: false,
    });
    if (!gl) {
      throw new Error("WebGL2 is not available in this browser.");
    }
    this.gl = gl;
    const quadBuffer = gl.createBuffer();
    if (!quadBuffer) {
      throw new Error("Could not allocate WebGL viewer geometry.");
    }
    this.quadBuffer = quadBuffer;
    gl.bindBuffer(gl.ARRAY_BUFFER, quadBuffer);
    gl.bufferData(
      gl.ARRAY_BUFFER,
      new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
      gl.STATIC_DRAW,
    );
  }

  render(options: WebglViewerRenderOptions): WebglViewerMetrics {
    const gl = this.gl;
    if (gl.isContextLost()) {
      throw new Error("WebGL context is lost.");
    }
    const compileStarted = performance.now();
    this.ensureProgram(options.ocioShader);
    const compileMs = this.lastCompileMs || performance.now() - compileStarted;
    this.lastCompileMs = 0;
    if (!this.program) {
      throw new Error("WebGL viewer program is unavailable.");
    }

    const uploadStarted = performance.now();
    const frameABytes = this.uploadFrameTexture("a", options.frameA);
    const frameBBytes = options.frameB ? this.uploadFrameTexture("b", options.frameB) : 0;
    this.uploadOcioTextures(options.ocioShader);
    this.assertGlOk("upload");
    const uploadMs = performance.now() - uploadStarted;

    const drawStarted = performance.now();
    const width = Math.max(1, Math.round(options.canvasCssSize.width * options.pixelRatio));
    const height = Math.max(1, Math.round(options.canvasCssSize.height * options.pixelRatio));
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;

    gl.viewport(0, 0, width, height);
    gl.disable(gl.BLEND);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.SCISSOR_TEST);
    gl.colorMask(true, true, true, true);
    gl.clearColor(0.067, 0.067, 0.067, 1);
    gl.clear(gl.COLOR_BUFFER_BIT);
    gl.useProgram(this.program);
    gl.bindBuffer(gl.ARRAY_BUFFER, this.quadBuffer);

    const position = gl.getAttribLocation(this.program, "a_position");
    gl.enableVertexAttribArray(position);
    gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

    this.bindTextureUniform("u_imageA", this.frameATexture, 0);
    this.bindTextureUniform("u_imageB", this.frameBTexture ?? this.frameATexture, 1);
    this.bindOcioTextureUniforms(options.ocioShader);

    this.uniform2f("u_canvasSize", options.canvasCssSize.width, options.canvasCssSize.height);
    this.uniform2f("u_imageSizeA", options.frameA.header.width, options.frameA.header.height);
    this.uniform2f(
      "u_imageSizeB",
      options.frameB?.header.width ?? options.frameA.header.width,
      options.frameB?.header.height ?? options.frameA.header.height,
    );
    this.uniform4f(
      "u_transform",
      options.transform.x,
      options.transform.y,
      Math.max(options.transform.scale, 0.0001),
      Math.max(options.pixelAspect, 0.0001),
    );
    this.uniform3f(
      "u_viewerProcess",
      Math.max(options.viewerProcess.gain, 0),
      Math.max(options.viewerProcess.saturation, 0),
      Number.isFinite(options.viewerProcess.fstop) ? options.viewerProcess.fstop : 0,
    );
    this.uniform1f("u_pixelRatio", options.pixelRatio);
    this.uniform1f("u_wipePosition", clamp(options.wipePosition, 0, 1));
    this.uniform1f("u_wipeAngle", options.wipeAngle);
    this.uniform1i("u_compareMode", options.compareMode === "difference" ? 2 : options.compareMode === "wipe" ? 1 : 0);
    this.uniform1i("u_hasB", options.frameB ? 1 : 0);
    this.uniform1i("u_applyOcio", options.frameA.header.apply_ocio ? 1 : 0);

    gl.drawArrays(gl.TRIANGLES, 0, 6);
    gl.flush();
    this.assertGlOk("draw");

    return {
      mode: "webgl-float",
      ocio_gpu: this.programUsesOcio,
      fallback_reason: this.fallbackReason,
      upload_ms: roundMs(uploadMs),
      draw_ms: roundMs(performance.now() - drawStarted),
      compile_ms: roundMs(compileMs),
      width: options.frameA.header.width,
      height: options.frameA.header.height,
      bytes: frameABytes + frameBBytes,
      timestamp: Date.now() / 1000,
    };
  }

  dispose() {
    const gl = this.gl;
    if (!gl.isContextLost()) {
      if (this.program) gl.deleteProgram(this.program);
      if (this.quadBuffer) gl.deleteBuffer(this.quadBuffer);
      if (this.frameATexture) gl.deleteTexture(this.frameATexture);
      if (this.frameBTexture) gl.deleteTexture(this.frameBTexture);
      for (const texture of this.ocioTextures.values()) gl.deleteTexture(texture);
    }
    this.program = null;
    this.frameATexture = null;
    this.frameBTexture = null;
    this.frameAData = null;
    this.frameBData = null;
    this.frameARevision = -1;
    this.frameBRevision = -1;
    this.ocioTextureKey = "";
    this.ocioTextures.clear();
  }

  isContextLost(): boolean {
    return this.gl.isContextLost();
  }

  private ensureProgram(shader: OcioGpuShader | null | undefined) {
    const shaderKey = shader?.available && shader.shader_text ? shader.shader_text : "fallback";
    if (this.program && this.programKey === shaderKey) return;

    const compileStarted = performance.now();
    const previous = this.program;
    this.program = null;
    this.programKey = shaderKey;
    this.programUsesOcio = false;
    this.fallbackReason = null;

    if (shader?.available && shader.shader_text) {
      const ocioSource = prepareOcioShader(shader);
      if (ocioSource) {
        try {
          this.program = this.createProgram(fragmentShaderSource(ocioSource, shader.function_name || "OCIODisplay"));
          this.programUsesOcio = true;
        } catch (error) {
          this.fallbackReason = error instanceof Error ? error.message : String(error);
        }
      } else {
        this.fallbackReason = shader.reason || "OCIO shader could not be translated for WebGL.";
      }
    } else if (shader && !shader.available) {
      this.fallbackReason = shader.reason || "OCIO GPU shader is unavailable.";
    }

    if (!this.program) {
      this.program = this.createProgram(fragmentShaderSource(null, "OCIODisplay"));
      this.programKey = `fallback:${this.fallbackReason ?? "no-ocio"}`;
    }
    if (previous) this.gl.deleteProgram(previous);
    this.lastCompileMs = performance.now() - compileStarted;
  }

  private createProgram(fragmentSource: string): WebGLProgram {
    const gl = this.gl;
    const vertex = compileShader(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
    const fragment = compileShader(gl, gl.FRAGMENT_SHADER, fragmentSource);
    const program = gl.createProgram();
    if (!program) {
      throw new Error("Could not allocate WebGL program.");
    }
    gl.attachShader(program, vertex);
    gl.attachShader(program, fragment);
    gl.linkProgram(program);
    gl.deleteShader(vertex);
    gl.deleteShader(fragment);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
      const log = gl.getProgramInfoLog(program) || "unknown WebGL link error";
      gl.deleteProgram(program);
      throw new Error(log);
    }
    return program;
  }

  private uploadFrameTexture(slot: "a" | "b", frame: FloatViewerFrame): number {
    const gl = this.gl;
    const currentData = slot === "a" ? this.frameAData : this.frameBData;
    const currentRevision = slot === "a" ? this.frameARevision : this.frameBRevision;
    const nextRevision = frame.header.tile_revision ?? 0;
    if (currentData === frame.pixels && currentRevision === nextRevision) return 0;

    let texture = slot === "a" ? this.frameATexture : this.frameBTexture;
    if (!texture) {
      texture = gl.createTexture();
      if (!texture) throw new Error("Could not allocate viewer frame texture.");
      if (slot === "a") this.frameATexture = texture;
      else this.frameBTexture = texture;
    }
    gl.bindTexture(gl.TEXTURE_2D, texture);
    setTextureParameters(gl);
    gl.texImage2D(
      gl.TEXTURE_2D,
      0,
      frame.header.dtype === "float16" ? gl.RGBA16F : gl.RGBA32F,
      frame.header.width,
      frame.header.height,
      0,
      gl.RGBA,
      frame.header.dtype === "float16" ? gl.HALF_FLOAT : gl.FLOAT,
      frame.pixels,
    );
    if (slot === "a") {
      this.frameAData = frame.pixels;
      this.frameARevision = nextRevision;
    } else {
      this.frameBData = frame.pixels;
      this.frameBRevision = nextRevision;
    }
    return frame.header.byte_length;
  }

  private uploadOcioTextures(shader: OcioGpuShader | null | undefined) {
    const gl = this.gl;
    const nextKey = shader?.available
      ? shader.textures.map((texture) => `${texture.sampler_name}:${texture.width}x${texture.height}:${texture.values.length}`).join("|")
      : "";
    if (this.ocioTextureKey === nextKey) return;
    this.ocioTextureKey = nextKey;
    for (const texture of this.ocioTextures.values()) gl.deleteTexture(texture);
    this.ocioTextures.clear();
    if (!shader?.available) return;

    for (const textureInfo of shader.textures) {
      const texture = gl.createTexture();
      if (!texture) continue;
      gl.bindTexture(gl.TEXTURE_2D, texture);
      setTextureParameters(gl);
      const rgba = expandOcioTexture(textureInfo);
      gl.texImage2D(
        gl.TEXTURE_2D,
        0,
        gl.RGBA32F,
        Math.max(textureInfo.width, 1),
        Math.max(textureInfo.height || 1, 1),
        0,
        gl.RGBA,
        gl.FLOAT,
        rgba,
      );
      this.ocioTextures.set(textureInfo.sampler_name, texture);
    }
  }

  private bindTextureUniform(name: string, texture: WebGLTexture | null, unit: number) {
    if (!this.program || !texture) return;
    const gl = this.gl;
    gl.activeTexture(gl.TEXTURE0 + unit);
    gl.bindTexture(gl.TEXTURE_2D, texture);
    this.uniform1i(name, unit);
  }

  private bindOcioTextureUniforms(shader: OcioGpuShader | null | undefined) {
    if (!shader?.available) return;
    let unit = 2;
    for (const texture of shader.textures) {
      this.bindTextureUniform(texture.sampler_name, this.ocioTextures.get(texture.sampler_name) ?? null, unit);
      unit += 1;
    }
  }

  private uniform1i(name: string, x: number) {
    if (!this.program) return;
    const location = this.gl.getUniformLocation(this.program, name);
    if (location) this.gl.uniform1i(location, x);
  }

  private uniform1f(name: string, x: number) {
    if (!this.program) return;
    const location = this.gl.getUniformLocation(this.program, name);
    if (location) this.gl.uniform1f(location, x);
  }

  private uniform2f(name: string, x: number, y: number) {
    if (!this.program) return;
    const location = this.gl.getUniformLocation(this.program, name);
    if (location) this.gl.uniform2f(location, x, y);
  }

  private uniform3f(name: string, x: number, y: number, z: number) {
    if (!this.program) return;
    const location = this.gl.getUniformLocation(this.program, name);
    if (location) this.gl.uniform3f(location, x, y, z);
  }

  private uniform4f(name: string, x: number, y: number, z: number, w: number) {
    if (!this.program) return;
    const location = this.gl.getUniformLocation(this.program, name);
    if (location) this.gl.uniform4f(location, x, y, z, w);
  }

  private assertGlOk(stage: string) {
    const gl = this.gl;
    if (gl.isContextLost()) {
      throw new Error(`WebGL context lost during ${stage}.`);
    }
    const error = gl.getError();
    if (error !== gl.NO_ERROR) {
      throw new Error(`${webglErrorName(gl, error)} during ${stage}.`);
    }
  }
}

function fragmentShaderSource(ocioSource: string | null, functionName: string): string {
  const displayBody = ocioSource
    ? `
${ocioSource}

vec4 displayTransform(vec4 color) {
  color = sanitizeColor(color);
  if (u_applyOcio == 1) {
    color = ${functionName}(color);
  }
  color = sanitizeColor(color);
  return vec4(clamp(color.rgb, 0.0, 1.0), clamp(color.a, 0.0, 1.0));
}
`
    : `
vec4 displayTransform(vec4 color) {
  color = sanitizeColor(color);
  if (u_applyOcio == 1) {
    color.rgb = pow(clamp(max(color.rgb, vec3(0.0)), vec3(0.0), vec3(1.0)), vec3(1.0 / 2.2));
  }
  color = sanitizeColor(color);
  return vec4(clamp(color.rgb, 0.0, 1.0), clamp(color.a, 0.0, 1.0));
}
`;

  return `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;

uniform sampler2D u_imageA;
uniform sampler2D u_imageB;
uniform vec2 u_canvasSize;
uniform vec2 u_imageSizeA;
uniform vec2 u_imageSizeB;
uniform vec4 u_transform;
uniform vec3 u_viewerProcess;
uniform float u_pixelRatio;
uniform float u_wipePosition;
uniform float u_wipeAngle;
uniform int u_compareMode;
uniform int u_hasB;
uniform int u_applyOcio;

out vec4 outColor;

vec3 sanitizeRgb(vec3 value) {
  bvec3 bad = bvec3(
    isnan(value.r) || isinf(value.r),
    isnan(value.g) || isinf(value.g),
    isnan(value.b) || isinf(value.b)
  );
  value = clamp(value, vec3(-65504.0), vec3(65504.0));
  return mix(value, vec3(0.0), bad);
}

vec4 sanitizeColor(vec4 value) {
  return vec4(sanitizeRgb(value.rgb), (isnan(value.a) || isinf(value.a)) ? 1.0 : clamp(value.a, -65504.0, 65504.0));
}

${displayBody}

vec4 viewerProcess(vec4 color) {
  color = sanitizeColor(color);
  float exposure = pow(2.0, u_viewerProcess.z) * u_viewerProcess.x;
  color.rgb *= exposure;
  float luma = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
  color.rgb = mix(vec3(luma), color.rgb, u_viewerProcess.y);
  return sanitizeColor(color);
}

vec4 sampleFrame(sampler2D image, vec2 size, vec2 coord) {
  if (coord.x < 0.0 || coord.y < 0.0 || coord.x >= size.x || coord.y >= size.y) {
    return vec4(-1.0);
  }
  return texture(image, (coord + vec2(0.5)) / max(size, vec2(1.0)));
}

vec4 checker(vec2 screen) {
  vec2 cell = floor(screen / 16.0);
  float value = mod(cell.x + cell.y, 2.0);
  return mix(vec4(0.062, 0.062, 0.062, 1.0), vec4(0.086, 0.086, 0.086, 1.0), value);
}

float wipeSide(vec2 screen, vec2 imageDisplaySize) {
  vec2 origin = u_transform.xy + vec2(imageDisplaySize.x * clamp(u_wipePosition, 0.0, 1.0), imageDisplaySize.y * 0.5) * u_transform.z;
  float radians = u_wipeAngle * 0.017453292519943295;
  vec2 normal = vec2(cos(radians), sin(radians));
  return dot(screen - origin, normal);
}

void main() {
  vec2 screen = vec2(gl_FragCoord.x / u_pixelRatio, u_canvasSize.y - gl_FragCoord.y / u_pixelRatio);
  vec2 imageDisplay = (screen - u_transform.xy) / u_transform.z;
  vec2 coord = vec2(imageDisplay.x / max(u_transform.w, 0.0001), imageDisplay.y);
  vec4 a = sampleFrame(u_imageA, u_imageSizeA, coord);
  vec4 b = u_hasB == 1 ? sampleFrame(u_imageB, u_imageSizeB, coord) : a;
  if (a.r < -0.5 && (u_compareMode != 1 || b.r < -0.5)) {
    outColor = checker(screen);
    return;
  }

  a = viewerProcess(max(a, vec4(0.0)));
  b = viewerProcess(max(b, vec4(0.0)));

  if (u_compareMode == 2 && u_hasB == 1) {
    outColor = displayTransform(vec4(abs(a.rgb - b.rgb), 1.0));
    return;
  }
  if (u_compareMode == 1 && u_hasB == 1) {
    vec2 displaySize = vec2(u_imageSizeA.x * max(u_transform.w, 0.0001), u_imageSizeA.y);
    outColor = wipeSide(screen, displaySize) >= 0.0 ? displayTransform(b) : displayTransform(a);
    return;
  }
  outColor = displayTransform(a);
}
`;
}

function compileShader(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader {
  const shader = gl.createShader(type);
  if (!shader) throw new Error("Could not allocate WebGL shader.");
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const log = gl.getShaderInfoLog(shader) || "unknown WebGL shader error";
    gl.deleteShader(shader);
    throw new Error(log);
  }
  return shader;
}

function prepareOcioShader(shader: OcioGpuShader): string | null {
  if (!shader.shader_text) return null;
  let source = shader.shader_text
    .replace(/^\s*#version[^\n]*(\n|$)/gm, "")
    .replace(/^\s*#extension[^\n]*(\n|$)/gm, "")
    .replace(/\blayout\s*\([^)]*\)\s*/g, "")
    .replace(/\bsampler1D\b/g, "sampler2D");

  for (const texture of shader.textures) {
    source = rewriteTextureCalls(source, texture.sampler_name);
  }
  return source;
}

function rewriteTextureCalls(source: string, samplerName: string): string {
  let output = "";
  let index = 0;
  while (index < source.length) {
    const nextTexture = nextFunctionCall(source, index);
    if (!nextTexture) {
      output += source.slice(index);
      break;
    }
    output += source.slice(index, nextTexture.start);
    const args = splitTopLevelArgs(nextTexture.args);
    if (args[0]?.trim() === samplerName && args[1]) {
      const bias = args[2] ? `, ${args[2].trim()}` : "";
      output += `texture(${samplerName}, vec2(${args[1].trim()}, 0.5)${bias})`;
    } else {
      output += source.slice(nextTexture.start, nextTexture.end);
    }
    index = nextTexture.end;
  }
  return output;
}

function nextFunctionCall(source: string, startAt: number): { start: number; end: number; args: string } | null {
  const textureIndex = source.indexOf("texture", startAt);
  const texture1DIndex = source.indexOf("texture1D", startAt);
  const start =
    textureIndex === -1
      ? texture1DIndex
      : texture1DIndex === -1
        ? textureIndex
        : Math.min(textureIndex, texture1DIndex);
  if (start === -1) return null;
  const name = source.startsWith("texture1D", start) ? "texture1D" : "texture";
  const open = source.indexOf("(", start + name.length);
  if (open === -1) return null;
  let depth = 0;
  for (let index = open; index < source.length; index += 1) {
    const character = source[index];
    if (character === "(") depth += 1;
    if (character === ")") {
      depth -= 1;
      if (depth === 0) {
        return {
          start,
          end: index + 1,
          args: source.slice(open + 1, index),
        };
      }
    }
  }
  return null;
}

function splitTopLevelArgs(args: string): string[] {
  const parts: string[] = [];
  let depth = 0;
  let start = 0;
  for (let index = 0; index < args.length; index += 1) {
    const character = args[index];
    if (character === "(" || character === "[" || character === "{") depth += 1;
    else if (character === ")" || character === "]" || character === "}") depth -= 1;
    else if (character === "," && depth === 0) {
      parts.push(args.slice(start, index));
      start = index + 1;
    }
  }
  parts.push(args.slice(start));
  return parts;
}

function expandOcioTexture(texture: OcioGpuTexture): Float32Array {
  const width = Math.max(texture.width, 1);
  const height = Math.max(texture.height || 1, 1);
  const texelCount = width * height;
  const source = texture.values;
  const rgba = new Float32Array(texelCount * 4);
  const channels = source.length >= texelCount * 4 ? 4 : source.length >= texelCount * 3 ? 3 : 1;
  for (let texel = 0; texel < texelCount; texel += 1) {
    const dst = texel * 4;
    const src = texel * channels;
    rgba[dst] = source[src] ?? 0;
    rgba[dst + 1] = channels > 1 ? source[src + 1] ?? 0 : rgba[dst];
    rgba[dst + 2] = channels > 2 ? source[src + 2] ?? 0 : rgba[dst];
    rgba[dst + 3] = channels > 3 ? source[src + 3] ?? 1 : 1;
  }
  return rgba;
}

function setTextureParameters(gl: WebGL2RenderingContext) {
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
}

function roundMs(value: number) {
  return Math.round(value * 100) / 100;
}

function clamp(value: number, min: number, max: number) {
  return Math.max(min, Math.min(max, value));
}

function webglErrorName(gl: WebGL2RenderingContext, error: number) {
  switch (error) {
    case gl.INVALID_ENUM:
      return "WebGL INVALID_ENUM";
    case gl.INVALID_VALUE:
      return "WebGL INVALID_VALUE";
    case gl.INVALID_OPERATION:
      return "WebGL INVALID_OPERATION";
    case gl.INVALID_FRAMEBUFFER_OPERATION:
      return "WebGL INVALID_FRAMEBUFFER_OPERATION";
    case gl.OUT_OF_MEMORY:
      return "WebGL OUT_OF_MEMORY";
    case gl.CONTEXT_LOST_WEBGL:
      return "WebGL CONTEXT_LOST";
    default:
      return `WebGL error ${error}`;
  }
}
