# Compositor overlay fonts

TTF filenames must match `apps/graphics/typography/catalog.py` (`FONT_FAMILY_FILES`).

## Bundled here (local / Docker image)

- `Arial Bold.ttf`
- `Rubik-Bold.ttf`
- `RobotoSlab-Bold.ttf`
- `OpenSans_Condensed-Bold.ttf`
- `BricolageGrotesque-VariableFont_opsz,wdth,wght.ttf`
- `Montserrat-Bold.ttf`
- `ShantellSans-Bold.ttf`
- `FiraMono-Bold.ttf` (also used for Departure Mono)
- `PermanentMarker-Regular.ttf`
- `Geologica_Auto-Bold.ttf`

## Staging

Copy the same files into `/usr/share/fonts/truetype/` (flat directory; exact filenames above).
The loader also searches nested paths under that root.
