// colorink_grayscale.slang — Colorink grayscale filter for ShaderGlass
//
// Implements the same two modes as the native overlay:
//   Mode 0 (default): OKLCh perceptual grayscale (OKLab L -> sRGB)
//   Mode 1:           BT.709 Luma (direct on sRGB, matches the old shader)
//
// Install: referenced by colorink_grayscale.slangp, which is pointed to by
// the Colorink-generated ShaderGlass profile (colorink_*.sgp).

#version 450

#pragma parameter Mode "Mode" 0.0 0.0 1.0 1.0

layout(push_constant) uniform Push
{
    // built-in parameters
    vec4 SourceSize;
    vec4 OriginalSize;
    vec4 OutputSize;
    uint FrameCount;

    // custom parameters
    float Mode;
} params;

layout(std140, set = 0, binding = 0) uniform UBO
{
    mat4 MVP;
} global;

//////////////////////////////////////////////////////////////////
// vertex shader (standard ShaderGlass boilerplate)

#pragma stage vertex

layout(location = 0) in vec4 Position;
layout(location = 1) in vec2 TexCoord;
layout(location = 0) out vec2 vTexCoord;

void main()
{
    gl_Position = global.MVP * Position;
    vTexCoord = TexCoord;
}

//////////////////////////////////////////////////////////////////
// fragment shader — the grayscale filter

#pragma stage fragment

layout(location = 0) in vec2 vTexCoord;
layout(location = 0) out vec4 FragColor;
layout(set = 0, binding = 2) uniform sampler2D Source;

vec3 srgbToLinear(vec3 c)
{
    vec3 lo = c / 12.92;
    vec3 hi = pow((c + 0.055) / 1.055, vec3(2.4));
    return mix(lo, hi, step(0.04045, c));
}

float linearToSrgb(float c)
{
    if (c <= 0.0031308)
        return 12.92 * c;
    return 1.055 * pow(c, 1.0 / 2.4) - 0.055;
}

void main()
{
    vec3 col = texture(Source, vTexCoord).rgb;
    float gray;

    if (params.Mode < 0.5)
    {
        // OKLCh: perceptual OKLab L
        vec3 lin = srgbToLinear(col);
        float l = 0.4122214708 * lin.r + 0.5363325363 * lin.g + 0.0514459929 * lin.b;
        float m = 0.2119034982 * lin.r + 0.6806995451 * lin.g + 0.1073969566 * lin.b;
        float s = 0.0883024619 * lin.r + 0.2817188376 * lin.g + 0.6299787005 * lin.b;
        l = sign(l) * pow(abs(l), 1.0 / 3.0);
        m = sign(m) * pow(abs(m), 1.0 / 3.0);
        s = sign(s) * pow(abs(s), 1.0 / 3.0);
        float L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s;
        gray = linearToSrgb(clamp(L * L * L, 0.0, 1.0));
    }
    else
    {
        // BT.709 Luma (direct on sRGB — same as the original Luma shader)
        gray = dot(col, vec3(0.2126, 0.7152, 0.0722));
    }

    FragColor = vec4(gray, gray, gray, 1.0);
}
