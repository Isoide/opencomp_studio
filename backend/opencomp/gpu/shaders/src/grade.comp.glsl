#version 450

layout(local_size_x = 16, local_size_y = 16, local_size_z = 1) in;

layout(set = 0, binding = 0, std430) readonly buffer SrcBuffer {
  vec4 src[];
} srcBuffer;

layout(set = 0, binding = 1, std430) writeonly buffer DstBuffer {
  vec4 dst[];
} dstBuffer;

layout(push_constant) uniform GradeParams {
  uint width;
  uint height;
  float gain;
  float multiply;
  float offset;
  float add;
  float invGamma;
  float _pad0;
} params;

void main() {
  ivec2 coord = ivec2(gl_GlobalInvocationID.xy);
  if (coord.x >= int(params.width) || coord.y >= int(params.height)) {
    return;
  }
  uint index = uint(coord.y) * params.width + uint(coord.x);
  vec4 color = srcBuffer.src[index];
  vec4 graded = color;
  graded.rgb = (graded.rgb * params.gain * params.multiply) + params.offset + params.add;
  vec3 safeRgb = max(graded.rgb, vec3(0.0));
  graded.rgb = pow(safeRgb, vec3(max(params.invGamma, 1e-6)));
  dstBuffer.dst[index] = graded;
}
