// The sky's brightest named stars, so the map has landmarks people know. Positions are J2000
// (degrees), to the arcminute: enough for a name on the map. Drawn as a small dot with its name,
// below the featured targets in priority; they open nothing (a click there blinks that spot).

export interface BrightStar {
  name: string;
  ra: number;
  dec: number;
}

export const BRIGHT_STARS: readonly BrightStar[] = [
  { name: "Sirius", ra: 101.287, dec: -16.716 },
  { name: "Canopus", ra: 95.988, dec: -52.696 },
  { name: "Alpha Centauri", ra: 219.902, dec: -60.834 },
  { name: "Arcturus", ra: 213.915, dec: 19.182 },
  { name: "Vega", ra: 279.235, dec: 38.784 },
  { name: "Capella", ra: 79.172, dec: 45.998 },
  { name: "Rigel", ra: 78.634, dec: -8.202 },
  { name: "Procyon", ra: 114.826, dec: 5.225 },
  { name: "Achernar", ra: 24.429, dec: -57.237 },
  { name: "Betelgeuse", ra: 88.793, dec: 7.407 },
  { name: "Hadar", ra: 210.956, dec: -60.373 },
  { name: "Altair", ra: 297.696, dec: 8.868 },
  { name: "Acrux", ra: 186.650, dec: -63.099 },
  { name: "Aldebaran", ra: 68.980, dec: 16.509 },
  { name: "Antares", ra: 247.352, dec: -26.432 },
  { name: "Spica", ra: 201.298, dec: -11.161 },
  { name: "Pollux", ra: 116.329, dec: 28.026 },
  { name: "Fomalhaut", ra: 344.413, dec: -29.622 },
  { name: "Deneb", ra: 310.358, dec: 45.280 },
  { name: "Regulus", ra: 152.093, dec: 11.967 },
  { name: "Polaris", ra: 37.955, dec: 89.264 },
];
