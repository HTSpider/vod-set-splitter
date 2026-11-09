# Character Assets

Choose your render folder in **Settings > Thumbnails > Character images**.

Recommended layout:

```text
character-assets/
  Captain Falcon/
    Default.png
    Black.png
    Red.png
  Fox/
    Default.png
    Red.png
    Blue.png
```

Flat filenames also work:

```text
Fox - Default.png
Fox - Red.png
Captain Falcon - Black.webp
```

Transparent PNG or WebP renders work best. The exporter matches the per-set Character and Skin fields against folder and filenames. If no matching image exists, the thumbnail contains a labeled placeholder instead of failing.

## Automatic character and skin detection

Thumbnail renders alone are not guaranteed to match the small artwork or gameplay HUD shown by a stream overlay. Reliable automatic matching requires one labeled reference crop for every character costume as it appears in that overlay, or matching Slippi replay data. Keep those separately under `_overlay-references/` when available; they can be used to add overlay-specific matching without changing the thumbnail assets.