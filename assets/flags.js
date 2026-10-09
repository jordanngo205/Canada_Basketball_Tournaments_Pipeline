// FIBA uses IOC-style three-letter codes; flag emoji need ISO 3166-1 alpha-2.
// Only the countries that actually appear in the data are mapped — add as new
// tournaments bring new teams in.
const ISO2 = {
  ARG: "AR", AUS: "AU", BAH: "BS", BRA: "BR", CAN: "CA", CHI: "CL", CHN: "CN",
  CIV: "CI", COL: "CO", CUB: "CU", CZE: "CZ", DOM: "DO", EGY: "EG", ESP: "ES",
  GER: "DE", HUN: "HU", ITA: "IT", JAM: "JM", JPN: "JP", LAT: "LV", MEX: "MX",
  NCA: "NI", NZL: "NZ", PAN: "PA", PAR: "PY", PHI: "PH", PUR: "PR", SEN: "SN",
  SLO: "SI", SSD: "SS", TUR: "TR", URU: "UY", USA: "US", VEN: "VE",
  FRA: "FR", GBR: "GB", SRB: "RS", LTU: "LT", GRE: "GR", NGR: "NG", MLI: "ML",
  KOR: "KR", BEL: "BE", POR: "PT", FIN: "FI", SWE: "SE", ISR: "IL", UKR: "UA",
};

// Regional indicator symbols: 'A' is U+1F1E6, so offset each letter from 'A'.
function flag(code) {
  const iso = ISO2[code];
  if (!iso) return "";
  return String.fromCodePoint(...[...iso].map(c => 0x1f1e6 + c.charCodeAt(0) - 65));
}
