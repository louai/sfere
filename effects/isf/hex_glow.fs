/*{
    "DESCRIPTION": "A hexagon grid that lights up around a point (the ball) and pulses with the bass. Written for sfere.",
    "CREDIT": "sfere",
    "ISFVSN": "2",
    "CATEGORIES": ["Generator"],
    "INPUTS": [
        {"NAME": "center", "TYPE": "point2D", "DEFAULT": [0.5, 0.5], "MIN": [0, 0], "MAX": [1, 1]},
        {"NAME": "scale", "TYPE": "float", "DEFAULT": 14.0, "MIN": 3.0, "MAX": 60.0},
        {"NAME": "reach", "TYPE": "float", "DEFAULT": 0.35, "MIN": 0.05, "MAX": 1.5},
        {"NAME": "lineColor", "TYPE": "color", "DEFAULT": [0.2, 0.8, 1.0, 1.0]}
    ]
}*/
float hexDist(vec2 p) {
    p = abs(p);
    return max(dot(p, normalize(vec2(1.0, 1.7320508))), p.x);
}
void main() {
    vec2 res = RENDERSIZE;
    vec2 asp = vec2(res.x / res.y, 1.0);
    vec2 p = isf_FragNormCoord * asp * scale;
    vec2 s = vec2(1.0, 1.7320508);
    vec2 a = mod(p, s) - s * 0.5;
    vec2 b = mod(p - s * 0.5, s) - s * 0.5;
    vec2 g = dot(a, a) < dot(b, b) ? a : b;
    float edge = smoothstep(0.06, 0.0, 0.5 - hexDist(g));
    float d = length((isf_FragNormCoord - center) * asp);
    float lit = exp(-d / (reach * (0.6 + uBass)));
    gl_FragColor = vec4(lineColor.rgb * edge * lit * (0.4 + 0.8 * uBeat), 1.0);
}
