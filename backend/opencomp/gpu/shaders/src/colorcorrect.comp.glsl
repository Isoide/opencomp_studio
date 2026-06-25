#version 450

layout(local_size_x = 16, local_size_y = 16, local_size_z = 1) in;

layout(set = 0, binding = 0, std430) readonly buffer SrcBuffer {
  vec4 src[];
} srcBuffer;

layout(set = 0, binding = 1, std430) writeonly buffer DstBuffer {
  vec4 dst[];
} dstBuffer;

layout(push_constant) uniform ColorCorrectParams {
  uint width;
  uint height;
  float saturation;
  float contrast;
  float invGamma;
  float gain;
  float offset;
  float mixValue;
  float clampMode;
  float _pad0;
} params;

void main() {
  ivec2 coord = ivec2(gl_GlobalInvocationID.xy);
  if (coord.x >= int(params.width) || coord.y >= int(params.height)) {
    return;
  }
  uint index = uint(coord.y) * params.width + uint(coord.x);
  vec4 src = srcBuffer.src[index];
  vec3 lumaBase = vec3(dot(src.rgb, vec3(0.2126, 0.7152, 0.0722)));
  vec3 rgb = lumaBase + (src.rgb - lumaBase) * params.saturation;
  rgb = pow(max(rgb / 0.18, vec3(0.0)), vec3(params.contrast)) * 0.18;
  rgb = pow(max(rgb, vec3(0.0)), vec3(max(params.invGamma, 1e-6)));
  rgb = rgb * params.gain + params.offset;
  if (params.clampMode >= 0.5) {
    rgb = clamp(rgb, 0.0, 1.0);
  }
  vec3 mixed = mix(src.rgb, rgb, params.mixValue);
  dstBuffer.dst[index] = vec4(mixed, src.a);
}
