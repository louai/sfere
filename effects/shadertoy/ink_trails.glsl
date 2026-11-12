// Image: shows Buffer A (iChannel0) with a little contrast. Written for sfere.
void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec3 c = texture(iChannel0, fragCoord / iResolution.xy).rgb;
    fragColor = vec4(c * c * 0.9, 1.0);
}
