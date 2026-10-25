// WaveDrom draws into an inline SVG whose look comes entirely from the skin's
// <style> element. This stylesheet replaces the stock "default" skin's CSS so
// the waveform matches the page: dark background, light traces, blue inputs,
// green outputs. Class names are WaveDrom's own (s1..s16, see skins/default.js).
export const WAVE_SKIN_CSS = `
text{font-size:11pt;font-style:normal;font-variant:normal;font-weight:normal;font-stretch:normal;text-align:center;fill-opacity:1;font-family:ui-monospace,"JetBrains Mono","SFMono-Regular",Menlo,Consolas,monospace;fill:#e6e8ef}
.h1{font-size:33pt;font-weight:bold}.h2{font-size:27pt;font-weight:bold}.h3{font-size:20pt;font-weight:bold}.h4{font-size:14pt;font-weight:bold}.h5{font-size:11pt;font-weight:bold}.h6{font-size:8pt;font-weight:bold}
.muted{fill:#8b93a7}.warning{fill:#e0af68}.error{fill:#f7768e}.info{fill:#7aa2f7}.success{fill:#9ece6a}
.s1{fill:none;stroke:currentColor;stroke-width:1.2;stroke-linecap:round;stroke-linejoin:miter;stroke-miterlimit:4;stroke-opacity:1;stroke-dasharray:none}
.s2{fill:none;stroke:#2b374b;stroke-width:0.6;stroke-linecap:round;stroke-linejoin:miter;stroke-miterlimit:4;stroke-opacity:1;stroke-dasharray:none}
.s3{color:#000;fill:none;stroke:#5b6478;stroke-width:1;stroke-linecap:round;stroke-linejoin:miter;stroke-miterlimit:4;stroke-dasharray:1, 3;stroke-dashoffset:0;marker:none;visibility:visible;display:inline;overflow:visible}
.s4{fill:none;stroke:currentColor;stroke-width:1;stroke-linecap:round;stroke-linejoin:miter;stroke-miterlimit:4;stroke-dasharray:none;stroke-dashoffset:0;marker:none;visibility:visible;display:inline;overflow:visible}
.s5{fill:#1a2231;stroke:none}
.s6{fill:#e6e8ef;fill-opacity:1;stroke:none}
.s7{color:#000;fill:#1a2231;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s8{color:#000;fill:#3d3a2a;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s9{color:#000;fill:#3e2f26;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s10{color:#000;fill:#1f3a6b;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s11{color:#000;fill:#1e3f45;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s12{color:#000;fill:#2d4a23;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s13{color:#000;fill:#3b2a4d;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s14{color:#000;fill:#4a2a33;fill-opacity:1;fill-rule:nonzero;stroke:none;stroke-width:1px;marker:none;visibility:visible;display:inline;overflow:visible}
.s15{fill:#7aa2f7;fill-opacity:1;stroke:none}
.s16{fill:none;stroke:#7aa2f7;stroke-width:1;stroke-linecap:round;stroke-linejoin:miter;stroke-miterlimit:4;stroke-opacity:1;stroke-dasharray:none}
`;
