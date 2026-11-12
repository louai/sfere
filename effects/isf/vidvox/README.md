# Vidvox ISF shaders

The 197 shaders from [Vidvox/ISF-Files](https://github.com/Vidvox/ISF-Files) (commit `395072d`, MIT, see `LICENSE`) that load and draw in sfere.

Left out:
- transitions, which blend two video sources;
- shaders with custom vertex shaders, audio inputs or imported images;
- 13 that don't compile with Mesa or draw nothing on their own.

Most are filters: they process the layers drawn below them in a scene, so put them after a generator. Two are non-commercial: `Boxinator` and `ASCII Art` (see their `.toml`).
