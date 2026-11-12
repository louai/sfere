// Buffer A: advects last frame (iChannel0 = itself) in a slow curl and injects ink at the ball.
vec2 curl(vec2 p, float t) {
    float e = 0.01;
    float n1 = sin(p.x * 3.1 + t) * cos(p.y * 2.7 - t * 0.7);
    float n2 = sin((p.x + e) * 3.1 + t) * cos(p.y * 2.7 - t * 0.7);
    float n3 = sin(p.x * 3.1 + t) * cos((p.y + e) * 2.7 - t * 0.7);
    return vec2(n3 - n1, -(n2 - n1)) / e;
}
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 res = iResolution.xy;
    vec2 uv = fragCoord / res;
    vec2 p = vec2(uv.x * res.x / res.y, uv.y);
    vec2 vel = curl(p * 1.5, iTime * 0.2) * 0.0009;
    vec3 prev = texture(iChannel0, uv - vel).rgb * 0.985;
    vec2 m = iMouse.xy;
    float d = length((fragCoord - m) / res.y);
    float r = max(uBall.z, 0.01) * 1.8;
    vec3 ink = 0.5 + 0.5 * cos(6.2831 * (vec3(0.05, 0.4, 0.75) + iTime * 0.07));
    float inject = (iMouse.z >= 0.0 ? 1.0 : 0.0) * exp(-pow(d / r, 2.0)) * (0.25 + 0.6 * uBeat);
    fragColor = vec4(min(prev + ink * inject, vec3(4.0)), 1.0);
}
