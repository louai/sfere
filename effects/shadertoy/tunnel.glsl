// Rings rushing toward the viewer, centred on the ball; speed follows the tempo,
// a flash on each beat. Written for sfere.
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 res = iResolution.xy;
    vec2 c = iMouse.z >= 0.0 ? iMouse.xy : res * 0.5;
    vec2 p = (fragCoord - c) / res.y;
    float r = length(p) + 1e-3;
    float a = atan(p.y, p.x);
    float speed = (uBpm > 0.0 ? uBpm / 60.0 : 2.0) * 0.5;
    float z = 0.3 / r + iTime * speed;
    float rings = pow(0.5 + 0.5 * cos(z * 6.2831), 8.0);
    float spokes = pow(0.5 + 0.5 * cos(a * 8.0 + z * 1.5), 12.0) * 0.5;
    float fade = smoothstep(0.0, 0.25, r) * exp(-r * 1.2);
    vec3 col = 0.55 + 0.45 * cos(6.2831 * (vec3(0.6, 0.75, 0.95) + z * 0.05));
    fragColor = vec4(col * (rings + spokes) * fade * (0.35 + 1.2 * uBeat + 0.5 * uBass), 1.0);
}
