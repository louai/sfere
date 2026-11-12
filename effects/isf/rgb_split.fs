/*{
    "DESCRIPTION": "Filter: splits the scene so far into red/green/blue copies pushed away from a point (the ball). Written for sfere.",
    "CREDIT": "sfere",
    "ISFVSN": "2",
    "CATEGORIES": ["Filter"],
    "INPUTS": [
        {"NAME": "inputImage", "TYPE": "image"},
        {"NAME": "center", "TYPE": "point2D", "DEFAULT": [0.5, 0.5], "MIN": [0, 0], "MAX": [1, 1]},
        {"NAME": "amount", "TYPE": "float", "DEFAULT": 0.012, "MIN": 0.0, "MAX": 0.08}
    ]
}*/
void main() {
    vec2 dir = isf_FragNormCoord - center;
    float k = amount * (0.4 + 1.6 * uBeat);
    float r = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord + dir * k).r;
    float g = IMG_THIS_NORM_PIXEL(inputImage).g;
    float b = IMG_NORM_PIXEL(inputImage, isf_FragNormCoord - dir * k).b;
    gl_FragColor = vec4(r, g, b, 1.0);
}
