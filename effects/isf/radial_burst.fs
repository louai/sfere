/*{
    "DESCRIPTION": "Rays bursting from a point (the ball), flaring on beats. Written for sfere.",
    "CREDIT": "sfere",
    "ISFVSN": "2",
    "CATEGORIES": ["Generator"],
    "INPUTS": [
        {"NAME": "center", "TYPE": "point2D", "DEFAULT": [0.5, 0.5], "MIN": [0, 0], "MAX": [1, 1]},
        {"NAME": "rays", "TYPE": "float", "DEFAULT": 18.0, "MIN": 3.0, "MAX": 64.0},
        {"NAME": "intensity", "TYPE": "float", "DEFAULT": 0.8, "MIN": 0.0, "MAX": 3.0},
        {"NAME": "tint", "TYPE": "color", "DEFAULT": [1.0, 0.45, 0.9, 1.0]},
        {"NAME": "spin", "TYPE": "bool", "DEFAULT": true}
    ]
}*/
void main() {
    vec2 res = RENDERSIZE;
    vec2 p = (isf_FragNormCoord - center) * vec2(res.x / res.y, 1.0);
    float r = length(p);
    float a = atan(p.y, p.x) + (spin ? TIME * 0.3 : 0.0);
    float ray = pow(0.5 + 0.5 * cos(a * rays), 6.0);
    float fall = exp(-r * (3.0 - 1.5 * uBeat));
    vec3 c = tint.rgb * ray * fall * intensity * (0.3 + uBeat + 0.4 * uBass);
    gl_FragColor = vec4(c, 1.0);
}
