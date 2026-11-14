# Earth coastline vectors

`earth-land.json` contains only polygon rings (longitude/latitude, rounded to three decimals)
from Natural Earth's `ne_110m_land.geojson`, downloaded 14 November 2026. 127 polygons,
5,143 vertices. The source is public domain under the [Natural Earth terms](https://www.naturalearthdata.com/about/terms-of-use/).

Source: <https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_110m_land.geojson>

SHA-256 of the downloaded source: `9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9`.

Canvas rasterizes these rings into a land mask at runtime. Ocean, vegetation/desert colours,
clouds and the other planets' materials are procedural illustrations, not measured maps or
planet positions. No bitmap Earth or planetary image is bundled or generated as an asset.
The actual SPHEREx sky background remains separate from these decorative foreground objects.
