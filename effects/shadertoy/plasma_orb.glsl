// Swirling plasma around the ball (iMouse follows the ball). Written for sfere.
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 res = iResolution.xy;
    vec2 m = iMouse.z >= 0.0 ? iMouse.xy : res * 0.5;
    vec2 p = (fragCoord - m) / res.y;
    float r = length(p);
    float a = atan(p.y, p.x);
    float t = iTime * 0.6;
    float swirl = sin(a * 5.0 + t * 2.0 - r * 18.0 + sin(r * 9.0 - t * 3.0));
    float bands = 0.5 + 0.5 * swirl;
    float halo = exp(-r * (5.5 - 2.5 * uSub)) * (0.55 + 0.9 * uBeat);
    vec3 col = 0.5 + 0.5 * cos(6.2831 * (vec3(0.0, 0.33, 0.67) + a / 6.2831 + t * 0.1));
    fragColor = vec4(col * bands * halo * (0.6 + 0.6 * uBall.w) * glow, 1.0);
}
