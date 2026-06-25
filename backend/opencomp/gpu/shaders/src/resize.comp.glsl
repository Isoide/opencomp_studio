#version 450

layout(local_size_x = 16, local_size_y = 16, local_size_z = 1) in;

layout(set = 0, binding = 0, std430) readonly buffer SrcBuffer {
  vec4 src[];
} srcBuffer;

layout(set = 0, binding = 1, std430) writeonly buffer DstBuffer {
  vec4 dst[];
} dstBuffer;

layout(push_constant) uniform ResizeParams {
  uint sourceWidth;
  uint sourceHeight;
  uint targetWidth;
  uint targetHeight;
} params;

uint srcIndex(uint x, uint y) {
  return y * params.sourceWidth + x;
}

uint dstIndex(uint x, uint y) {
  return y * params.targetWidth + x;
}

void main() {
  ivec2 coord = ivec2(gl_GlobalInvocationID.xy);
  if (coord.x >= int(params.targetWidth) || coord.y >= int(params.targetHeight)) {
    return;
  }

  vec2 scale = vec2(float(params.sourceWidth) / float(params.targetWidth), float(params.sourceHeight) / float(params.targetHeight));
  vec2 srcCoord = (vec2(coord) + vec2(0.5)) * scale - vec2(0.5);
  vec2 baseCoord = floor(srcCoord);
  vec2 fracCoord = srcCoord - baseCoord;

  uint x0 = uint(clamp(int(baseCoord.x), 0, int(params.sourceWidth) - 1));
  uint y0 = uint(clamp(int(baseCoord.y), 0, int(params.sourceHeight) - 1));
  uint x1 = uint(clamp(int(baseCoord.x) + 1, 0, int(params.sourceWidth) - 1));
  uint y1 = uint(clamp(int(baseCoord.y) + 1, 0, int(params.sourceHeight) - 1));

  vec4 c00 = srcBuffer.src[srcIndex(x0, y0)];
  vec4 c10 = srcBuffer.src[srcIndex(x1, y0)];
  vec4 c01 = srcBuffer.src[srcIndex(x0, y1)];
  vec4 c11 = srcBuffer.src[srcIndex(x1, y1)];

  vec4 cx0 = mix(c00, c10, fracCoord.x);
  vec4 cx1 = mix(c01, c11, fracCoord.x);
  dstBuffer.dst[dstIndex(uint(coord.x), uint(coord.y))] = mix(cx0, cx1, fracCoord.y);
}
