/* imgui-bundle's native module links against GLFW's X11 accessors, which a
 * Wayland-only GLFW build does not export. sfere only uses imgui through the
 * Python backends (no native window handles), so these stubs are never used for
 * anything; they exist so the module can load next to the Wayland GLFW.
 * Built automatically by sfere/compat.py. */
#include <stddef.h>
void *glfwGetX11Display(void) { return NULL; }
unsigned long glfwGetX11Window(void *window) { (void)window; return 0; }
