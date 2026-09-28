// =====================================================================
// navigation.js -- Campus Navigator: the Leaflet map, From/To, live GPS.
// Talks to navigation_routes.py (/api/campus, /api/route).
// Uses api() from app.js.
// =====================================================================

// ---------- Campus Navigator: Leaflet map + live GPS + Flask /api/route ----------
// The walking routes come from the server (campus_graph.py builds the footpath
// network from the campus OpenStreetMap export). "From" is either the student's
// current location (followed live, the route updates as they walk) or a building.
const NAV_ARRIVE_M = 20;           // this close to the building (or the end of the path) = arrived
const NAV_MIN_MOVE_M = 5;          // don't ask for a new route unless the student moved this far...
const NAV_MIN_INTERVAL_MS = 3000;  // ...and at least this long has passed
const NAV_MAX_ACCURACY_M = 100;    // ignore live GPS updates that are less accurate than this
// desk testing: open /?simulate=1#navigate and click the map to "walk" there
const navSimulate = new URLSearchParams(location.search).has('simulate');

const NAV_COLORS = {
  route: '#1d5fd1',        // route line
  building: '#0b3d91',     // building markers (UNIZULU blue)
  user: '#1a73e8',         // "you are here" dot
  destination: '#c63c3c',  // ring around the destination
};

// ---------- Styles for the map pins, place cards, fading and zoom hint ----------
// They live here (not in style.css) so this one file carries the whole feature:
// replacing navigation.js is enough, even if an older style.css is still cached.
const NAV_VERSION = '2026-09-27c';
const NAV_STYLES = `
/* ===== Campus map pins (navigation.js picks one per place) =====
   Each pin is a rotated square with one sharp corner = a map-pin teardrop.
   Widths must match NAV_PIN_SIZES in navigation.js. */
.campus-pin-icon{ background:none; border:none; display:flex; align-items:center; justify-content:center; }
.campus-pin{
  position: relative;
  display:flex; align-items:center; justify-content:center;
  cursor: pointer;
  transition: transform .15s ease, box-shadow .15s ease;
}
.campus-pin .pin-ring,
.campus-pin .pin-inner{ display:flex; align-items:center; justify-content:center; border-radius:50%; }
.campus-pin svg{ display:block; }

/* building.html: white pin, slate circle, graduation cap */
.pin-building{
  width:36px; height:36px; background:#fff;
  border-radius:50%; border-bottom-right-radius:3px;
  transform: rotate(45deg);
  box-shadow: 0 4px 6px rgba(0,0,0,.18), 0 1px 3px rgba(0,0,0,.12);
}
.pin-building:hover{ transform: rotate(45deg) scale(1.08); }
.pin-building .pin-inner{ width:24px; height:24px; background:#677f8e; transform: rotate(-45deg); }
.pin-building svg{ width:14px; height:14px; fill:#fff; }

/* clinic_icon.html: teal 3D pin, white ring, plus sign */
.pin-clinic{
  width:42px; height:42px;
  border-radius: 50% 50% 50% 0;
  transform: rotate(-45deg);
  background: linear-gradient(135deg, #00c9b6 0%, #009e8e 100%);
  box-shadow: 0 5px 8px rgba(0,158,142,.4), 0 2px 4px rgba(0,0,0,.2),
              inset 0 -3px 5px rgba(0,0,0,.2), inset 0 3px 5px rgba(255,255,255,.5);
}
.pin-clinic:hover{ transform: rotate(-45deg) scale(1.08); }
.pin-clinic .pin-ring{ width:32px; height:32px; background:#fff; box-shadow: inset 0 2px 4px rgba(0,0,0,.1); }
.pin-clinic .pin-inner{
  width:27px; height:27px; transform: rotate(45deg);
  background: radial-gradient(circle at 50% 30%, #f0f0f0 0%, #e0e0e0 60%, #d0d0d0 100%);
  box-shadow: inset 0 2px 4px rgba(255,255,255,.8), inset 0 -2px 4px rgba(0,0,0,.15), 0 1px 3px rgba(0,0,0,.1);
}
.pin-clinic svg{ width:16px; height:16px; fill:#009e8e; filter: drop-shadow(0 1px 1px rgba(0,0,0,.2)); }

/* res_entrance.html: charcoal pin, white circle, person-scan outline */
.pin-resEntrance{
  width:39px; height:39px; background:#333;
  border-radius: 50% 50% 50% 0;
  transform: rotate(-45deg);
  box-shadow: 0 4px 8px rgba(0,0,0,.25);
}
.pin-resEntrance:hover{ transform: rotate(-45deg) scale(1.08); }
.pin-resEntrance .pin-inner{ width:28px; height:28px; background:#fff; transform: rotate(45deg); }
.pin-resEntrance svg{ width:18px; height:18px; stroke:#333; stroke-width:2; fill:none; stroke-linecap:round; stroke-linejoin:round; }

/* maingate.html: white pin, dark slate circle, boom gate */
.pin-gate{
  width:48px; height:48px; background:#fff;
  border-radius:50%; border-bottom-right-radius:6px;
  transform: rotate(45deg);
  box-shadow: 0 4px 8px rgba(0,0,0,.18), 0 1px 3px rgba(0,0,0,.12);
}
.pin-gate:hover{ transform: rotate(45deg) scale(1.08); }
.pin-gate .pin-inner{ width:34px; height:34px; background:#384c5c; transform: rotate(-45deg); }
.pin-gate svg{ width:20px; height:20px; stroke:#fff; stroke-width:2; fill:none; stroke-linecap:round; stroke-linejoin:round; }

/* residence.html: charcoal pin, white circle, house */
.pin-residence{
  width:36px; height:36px; background:#404040;
  border-radius:50%; border-bottom-right-radius:5px;
  transform: rotate(45deg);
  box-shadow: 0 3px 6px rgba(0,0,0,.25);
}
.pin-residence:hover{ transform: rotate(45deg) scale(1.08); }
.pin-residence .pin-inner{ width:26px; height:26px; background:#fff; transform: rotate(-45deg); }
.pin-residence svg{ width:15px; height:15px; fill:#404040; }

/* library_icon.html: grey pin, white circle, open book */
.pin-library{
  width:36px; height:36px; background:#555;
  border-radius: 50% 50% 50% 0;
  transform: rotate(-45deg);
  box-shadow: 0 3px 6px rgba(0,0,0,.2);
}
.pin-library:hover{ transform: rotate(-45deg) scale(1.08); }
.pin-library .pin-inner{ width:27px; height:27px; background:#fff; transform: rotate(45deg); }
.pin-library svg{ width:16px; height:16px; stroke:#555; stroke-width:2; fill:none; stroke-linecap:round; stroke-linejoin:round; }

/* the destination of the current route: its pin grows and glows red */
.pin-is-destination .campus-pin{
  filter: drop-shadow(0 0 4px rgba(198,60,60,.9)) drop-shadow(0 0 10px rgba(198,60,60,.55));
  scale: 1.2;
  transform-origin: 50% 50%;
}

/* phones and tablets zoom with two fingers, so the +/- buttons are not needed
   there and cannot sit on top of a building pin */
@media (hover: none) and (pointer: coarse){
  #campusMap .leaflet-control-zoom{ display: none; }
}

/* pins fade out when zoomed out; the route's destination stays */
.campus-pin-icon{ transition: opacity .35s ease; }
#campusMap.pins-faded .campus-pin-icon:not(.pin-is-destination){ opacity: 0; pointer-events: none; }
.zoom-hint{
  position:absolute; left:50%; bottom: 14px; transform: translateX(-50%);
  margin:0; z-index: 500;
  background: rgba(14,30,60,.78); color:#fff;
  font-size: 12.5px; font-weight: 600;
  padding: 6px 14px; border-radius: 16px;
  pointer-events: none; white-space: nowrap;
}

/* ===== Place card (tooltip.html) — opens from a map pin ===== */
.place-popup .leaflet-popup-content-wrapper{
  padding: 0;
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 4px 12px rgba(0,0,0,.15), 0 2px 4px rgba(0,0,0,.1);
}
.place-popup .leaflet-popup-content{ margin: 0; width: min(300px, 78vw) !important; line-height: normal; }
.place-popup .leaflet-popup-tip{ box-shadow: 0 2px 4px rgba(0,0,0,.1); }
/* card placed under the pin (no room above): the arrow points up instead */
.place-popup.place-below .leaflet-popup-tip-container{ top: -20px; transform: rotate(180deg); }
.place-popup a.leaflet-popup-close-button{
  top: 8px; right: 8px;
  width: 26px; height: 26px;
  border-radius: 50%;
  background: rgba(0,0,0,.45);
  color: #fff;
  font: 18px/26px Arial, sans-serif;
  z-index: 4;
}
.place-popup a.leaflet-popup-close-button:hover{ background: rgba(0,0,0,.7); color:#fff; }
.place-card{ display:flex; flex-direction:column; font-family: inherit; }
.place-slider{ position: relative; height: 108px; background: #dcdcdc; overflow: hidden; }
.place-track{
  display:flex; height:100%;
  overflow-x: auto;
  scroll-snap-type: x mandatory;
  scroll-behavior: smooth;
  scrollbar-width: none;
}
.place-track::-webkit-scrollbar{ display:none; }
.place-slide{ flex: 0 0 100%; height:100%; scroll-snap-align: start; background-size: cover; background-position: center; }
.place-arrow{
  position:absolute; top:50%; transform: translateY(-50%);
  width:28px; height:28px; padding:0;
  border:none; border-radius:50%;
  background: rgba(0,0,0,.4); color:#fff;
  display:flex; align-items:center; justify-content:center;
  cursor:pointer; z-index:3;
  transition: background-color .2s ease;
}
.place-arrow:hover{ background: rgba(0,0,0,.7); }
.place-arrow svg{ width:16px; height:16px; fill: currentColor; }
.place-arrow-left{ left: 8px; }
.place-arrow-right{ right: 8px; }
.place-dots{ position:absolute; bottom:10px; left:50%; transform: translateX(-50%); display:flex; gap:6px; z-index:2; }
.place-dot{ width:6px; height:6px; border-radius:50%; background: rgba(255,255,255,.6); transition: all .2s ease; }
.place-dot.active{ background:#fff; transform: scale(1.2); }
.place-content{ padding: 10px 14px 12px; display:flex; flex-direction:column; gap: 5px; }
.place-title{ margin:0; font-size: 16px; font-weight: 600; color: #202124; line-height: 1.3; }
.place-details{ display:flex; align-items:center; flex-wrap:wrap; gap:6px; font-size: 13.5px; color: #70757a; }
.place-sep{ color: #dadce0; }
.place-access{ display:inline-flex; }
.place-access svg{ width:18px; height:18px; fill:#1a73e8; }
.place-closed{ color: #d93025; }
.place-open, .place-hours{ color: #188038; }
.place-go{
  align-self: flex-start;
  margin-top: 3px;
  background: var(--blue); color:#fff;
  border:none; border-radius: 18px;
  padding: 7px 14px;
  font-size: 13px; font-weight: 600;
  cursor:pointer;
}
.place-go:hover{ background: var(--blue-hover); }

/* map size: fills the screen height */
.map-wrap{ position: relative; }
#campusMap{
  height: clamp(420px, calc(100vh - 400px), 820px);
  height: clamp(420px, calc(100dvh - 400px), 820px);
}
@media (max-width: 900px){
  #campusMap{ height: clamp(360px, calc(100dvh - 330px), 640px); }
}

`;
(function navInjectStyles(){
  if(document.getElementById('navStyles')) return;
  const style = document.createElement('style');
  style.id = 'navStyles';
  style.textContent = NAV_STYLES;
  document.head.appendChild(style);             // added last, so it wins over older copies of these rules
})();
console.info(`Campus Navigator ${NAV_VERSION} loaded (pins appear after 3 zoom-ins)`);

// ---------- Map pin icons (instead of plain dots) ----------
// Which icon each place gets is decided from its name (case does not matter).
// Anything not listed here uses the "building" icon (Admin, Agriculture, AL's,
// Art building, Chapel hall, D block, Human Movement Science, King Bhekuzulu,
// PSD, SC's, UNIZULU building, UNIZULU building 2, ...).
// If buildings.json ever gives a place an "icon" field (building, clinic,
// resEntrance, gate, residence, library), that is used instead.
const NAV_PLACE_RULES = [
  { kind: 'clinic',      words: ['clinic'] },
  { kind: 'library',     words: ['library'] },
  { kind: 'resEntrance', words: ['east entrance'] },
  { kind: 'gate',        words: ['main gate', 'emhlathuze entrance', 'highfield entrance', 'gate', 'entrance'] },
  { kind: 'residence',   words: ['madiba', 'middle east', 'siqalo', 'west res', 'residence', ' res '] },
];

// the SVG drawn inside each pin (from building.html, clinic_icon.html,
// res_entrance.html, maingate.html, residence.html and library_icon.html)
const NAV_PIN_SVGS = {
  building: '<svg viewBox="0 0 24 24"><path d="M12 3L1 9L12 15L21 10.09V17H23V9M5 13.18V17.18L12 21L19 17.18V13.18L12 17L5 13.18Z"/></svg>',
  clinic: '<svg viewBox="0 0 24 24"><path d="M19 13H13V19H11V13H5V11H11V5H13V11H19V13Z"/></svg>',
  resEntrance: '<svg viewBox="0 0 24 24"><path d="M12 4C9.5 4 7.5 6 7.5 9C7.5 11.5 8.5 13.5 9.5 14.5V17"/><path d="M12 4C14.5 4 16.5 6 16.5 9C16.5 11.5 15.5 13.5 14.5 14.5V17"/><path d="M6 21L9.5 17"/><path d="M18 21L14.5 17"/><path d="M2 6V2H6"/><path d="M18 2H22V6"/><path d="M2 18V22H6"/><path d="M18 22H22V18"/></svg>',
  gate: '<svg viewBox="0 0 24 24"><line x1="4" y1="21" x2="20" y2="21"/><rect x="6" y="8" width="7" height="13"/><line x1="5" y1="8" x2="14" y2="8"/><line x1="5" y1="7" x2="14" y2="7"/><rect x="7.5" y="10" width="4" height="4"/><line x1="8.5" y1="11" x2="10.5" y2="13" stroke-width="1.5"/><rect x="13" y="15" width="3" height="6"/><circle cx="14.5" cy="16" r="1" fill="#ffffff"/><rect x="15" y="9" width="9" height="4" transform="rotate(-30 15 13)"/><line x1="17.5" y1="10.5" x2="18.5" y2="12.5" transform="rotate(-30 15 13)" stroke-width="1.5"/><line x1="19.5" y1="10.5" x2="20.5" y2="12.5" transform="rotate(-30 15 13)" stroke-width="1.5"/><line x1="21.5" y1="10.5" x2="22.5" y2="12.5" transform="rotate(-30 15 13)" stroke-width="1.5"/></svg>',
  residence: '<svg viewBox="0 0 24 24"><path d="M12 3L2 12H5V21H10V15H14V21H19V12H22L12 3ZM12 5.7L17 10.2V19H16V13H8V19H7V10.2L12 5.7Z"/></svg>',
  library: '<svg viewBox="0 0 24 24"><path d="M2 4V18C2 18 5 17 8 17C10 17 12 18 12 18V6C12 6 10 5 8 5C5 5 2 4 2 4Z"/><path d="M22 4V18C22 18 19 17 16 17C14 17 12 18 12 18V6C12 6 14 5 16 5C19 5 22 4 22 4Z"/><line x1="12" y1="6" x2="12" y2="18"/><line x1="4" y1="5.5" x2="4" y2="16" stroke-width="2.5"/><line x1="20" y1="5.5" x2="20" y2="16" stroke-width="2.5"/></svg>',
};

// size = width of the pin in px (must match style.css); the pin's point
// sits 0.707 x size below its centre, which is where it touches the map
const NAV_PIN_SIZES = { building: 36, clinic: 42, resEntrance: 39, gate: 48, residence: 36, library: 36 };
const NAV_PIN_SHAPES = {};
Object.entries(NAV_PIN_SIZES).forEach(([kind, size]) => {
  const box = Math.ceil(size * 1.5);          // room for the rotated pin and its shadow
  const point = Math.round(size * 0.707);
  NAV_PIN_SHAPES[kind] = { box, anchorY: Math.round(box / 2) + point, tipOffset: point * 2 + 4 };
});

function navPlaceKind(bld){
  if(bld.icon && NAV_PIN_SVGS[bld.icon]) return bld.icon;
  const name = ' ' + String(bld.name || '').toLowerCase() + ' ';
  const rule = NAV_PLACE_RULES.find(r => r.words.some(w => name.includes(w)));
  return rule ? rule.kind : 'building';
}

const navPinIconCache = {};
function navPinIcon(kind){
  if(navPinIconCache[kind]) return navPinIconCache[kind];
  const shape = NAV_PIN_SHAPES[kind];
  const inner = kind === 'clinic'
    ? `<span class="pin-ring"><span class="pin-inner">${NAV_PIN_SVGS.clinic}</span></span>`
    : `<span class="pin-inner">${NAV_PIN_SVGS[kind]}</span>`;
  navPinIconCache[kind] = L.divIcon({
    className: 'campus-pin-icon',               // replaces Leaflet's default white box
    html: `<span class="campus-pin pin-${kind}">${inner}</span>`,
    iconSize: [shape.box, shape.box],
    iconAnchor: [shape.box / 2, shape.anchorY],  // the point of the pin marks the building
    popupAnchor: [0, -(shape.tipOffset - 4)],    // the place card opens just above the pin
  });
  return navPinIconCache[kind];
}

// ---------- Place card (shown when a pin is hovered or tapped) ----------
// Photos, accessibility and opening hours come from buildings.json when a place has them:
//   "images": ["img/places/library-1.jpg", "img/places/library-2.jpg"]
//   "accessible": true                         -> wheelchair symbol
//   "status": "Open" or "Closed"               -> green / red
//   "hours": "Opens 7:30 am Mon"
// Without "images" the campus photo (img/campus.jpg) is shown.
const NAV_KIND_LABELS = {
  building: 'Campus building', clinic: 'Clinic', resEntrance: 'Residence entrance',
  gate: 'Campus entrance', residence: 'Residence', library: 'Library',
};
const NAV_SVG_LEFT = '<svg viewBox="0 0 24 24"><path d="M15.41 7.41L14 6l-6 6 6 6 1.41-1.41L10.83 12z"/></svg>';
const NAV_SVG_RIGHT = '<svg viewBox="0 0 24 24"><path d="M8.59 16.59L10 18l6-6-6-6-1.41 1.41L13.17 12z"/></svg>';
const NAV_SVG_WHEELCHAIR = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 2C6.48 2 2 6.48 2 12C2 17.52 6.48 22 12 22C17.52 22 22 17.52 22 12C22 6.48 17.52 2 12 2ZM12 20C7.59 20 4 16.41 4 12C4 7.59 7.59 4 12 4C16.41 4 20 7.59 20 12C20 16.41 16.41 20 12 20ZM15.5 16.5C15.5 16.5 14.5 15.5 14.5 14C14.5 13.5 14 13 13.5 13H11.5V11H13.5C14.5 11 15.5 10 15.5 9C15.5 8 14.5 7 13.5 7H11.5V6H10.5V7H8.5C7.5 7 6.5 8 6.5 9C6.5 10 7.5 11 8.5 11H10.5V13H8.5C7.5 13 7 13.5 7 14C7 15.5 8 16.5 8 16.5L10 18.5L9 20.5L10.5 21.5L12 19L14 21L15.5 20L14 18L15.5 16.5Z"/></svg>';

function navEl(tag, className, text){
  const el = document.createElement(tag);
  if(className) el.className = className;
  if(text !== undefined) el.textContent = text;   // textContent: data is never treated as HTML
  return el;
}

function navPlaceImages(bld){
  if(Array.isArray(bld.images) && bld.images.length) return bld.images;
  if(Array.isArray(bld.photos) && bld.photos.length) return bld.photos;
  if(bld.image) return [bld.image];
  return ['img/campus.jpg'];
}

function navBuildSlider(images){
  const slider = navEl('div', 'place-slider');
  const track = navEl('div', 'place-track');
  images.forEach(src => {
    const slide = navEl('div', 'place-slide');
    slide.style.backgroundImage = `url(${JSON.stringify(String(src))})`;
    track.appendChild(slide);
  });
  slider.appendChild(track);
  if(images.length < 2) return slider;

  const dots = navEl('div', 'place-dots');
  images.forEach((_, i) => dots.appendChild(navEl('span', 'place-dot' + (i === 0 ? ' active' : ''))));
  const go = step => track.scrollBy({ left: step * track.clientWidth });
  const left = navEl('button', 'place-arrow place-arrow-left');
  const right = navEl('button', 'place-arrow place-arrow-right');
  left.type = right.type = 'button';
  left.setAttribute('aria-label', 'Previous image');
  right.setAttribute('aria-label', 'Next image');
  left.innerHTML = NAV_SVG_LEFT;
  right.innerHTML = NAV_SVG_RIGHT;
  left.addEventListener('click', e => { e.stopPropagation(); go(-1); });
  right.addEventListener('click', e => { e.stopPropagation(); go(1); });
  track.addEventListener('scroll', () => {
    const i = Math.round(track.scrollLeft / Math.max(1, track.clientWidth));
    dots.querySelectorAll('.place-dot').forEach((d, j) => d.classList.toggle('active', j === i));
  });
  slider.append(left, right, dots);
  return slider;
}

function navBuildPlaceCard(bld, kind){
  const card = navEl('div', 'place-card');
  card.appendChild(navBuildSlider(navPlaceImages(bld)));

  const content = navEl('div', 'place-content');
  content.appendChild(navEl('h3', 'place-title', bld.name));

  const details = navEl('div', 'place-details');
  const parts = [navEl('span', '', NAV_KIND_LABELS[kind] || 'Campus building')];
  if(bld.description && bld.description.toLowerCase() !== bld.name.toLowerCase()){
    parts.push(navEl('span', '', bld.description));
  }
  if(bld.accessible){
    const wc = navEl('span', 'place-access');
    wc.innerHTML = NAV_SVG_WHEELCHAIR;
    wc.title = 'Wheelchair accessible';
    parts.push(wc);
  }
  if(bld.status){
    const closed = String(bld.status).toLowerCase().startsWith('closed');
    parts.push(navEl('span', closed ? 'place-closed' : 'place-open', bld.status));
  }
  if(bld.hours) parts.push(navEl('span', 'place-hours', bld.hours));
  parts.forEach((part, i) => {
    if(i) details.appendChild(navEl('span', 'place-sep', '·'));
    details.appendChild(part);
  });
  content.appendChild(details);

  const go = navEl('button', 'place-go', '➹ Get directions');
  go.type = 'button';
  go.addEventListener('click', () => {
    navMap.closePopup();
    document.getElementById('toSelect').value = bld.id;
    getRoute();
  });
  content.appendChild(go);
  card.appendChild(content);
  return card;
}

// Places the card so it is never cut off by the edge of the map:
// above the pin when there is room, otherwise below it, and slid left/right
// near the sides (the little arrow keeps pointing at the pin).
const NAV_CARD_GAP = 8;        // space kept between the card and the edge of the map
function navFitPlaceCard(popup, marker, kind){
  const el = popup.getElement();
  if(!el || !navMap) return;
  const tipBox = el.querySelector('.leaflet-popup-tip-container');
  el.classList.remove('place-below');
  popup.options.offset = [0, 7];                 // Leaflet's normal spot: just above the pin
  if(tipBox) tipBox.style.marginLeft = '';
  popup.update();

  const size = navMap.getSize();
  const pt = navMap.latLngToContainerPoint(marker.getLatLng());
  const h = el.offsetHeight, w = el.offsetWidth;
  const pinTop = NAV_PIN_SHAPES[kind].tipOffset - 4; // height of the pin above its point
  const topIfAbove = pt.y - pinTop + 7 - 20 - h;     // 20 = the arrow under the card
  const bottomIfBelow = pt.y + 20 + h;

  let dy = 7;
  if(topIfAbove < NAV_CARD_GAP && (size.y - bottomIfBelow) > topIfAbove){
    dy = h + 40 + pinTop;                            // flip: card sits under the pin
    el.classList.add('place-below');
  }
  let dx = 0;
  const left = pt.x - w / 2, right = pt.x + w / 2;
  if(left < NAV_CARD_GAP) dx = NAV_CARD_GAP - left;
  else if(right > size.x - NAV_CARD_GAP) dx = (size.x - NAV_CARD_GAP) - right;

  popup.options.offset = [dx, dy];
  popup.update();
  // keep the arrow on the pin (it stays 12px away from the card's rounded corners)
  if(tipBox){
    const limit = w / 2 - 32;
    tipBox.style.marginLeft = `${-20 - Math.max(-limit, Math.min(limit, dx))}px`;
  }
}

// Pins fade out when the map is zoomed out (the whole campus in view) and fade
// in once the student zooms closer. The destination of a route always stays visible.
const NAV_PINS_EXTRA_ZOOM = 3;   // pins appear after 3 presses of + (3 zoom levels) past the whole-campus view
function navUpdatePinVisibility(){
  if(!navMap) return;
  // never above the map's closest zoom, or the pins could never be seen
  const showAt = Math.min(navMap.getMinZoom() + NAV_PINS_EXTRA_ZOOM, navMap.getMaxZoom());
  const hidden = navMap.getZoom() < showAt;
  navMap.getContainer().classList.toggle('pins-faded', hidden);
  if(hidden) navMap.closePopup();
  let hint = document.getElementById('zoomHint');
  if(!hint){                                     // older index.html without the hint: make it here
    hint = document.createElement('p');
    hint.id = 'zoomHint';
    hint.className = 'zoom-hint';
    hint.textContent = 'Zoom in to see buildings';
    navMap.getContainer().appendChild(hint);
  }
  hint.hidden = !hidden;
}

// Hover (mouse): the card opens, and stays while the pointer is on the pin or the card.
// Click / tap: the card opens and stays until closed (✕), another pin, or a tap on the map.
function navAttachPlaceCard(marker, bld, kind){
  const popup = L.popup({
    className: 'place-popup',
    maxWidth: 300, minWidth: 260,
    autoPan: false,
    closeButton: true,
  });
  let pinned = false, closeTimer = null, card = null;
  const cancelClose = () => { clearTimeout(closeTimer); closeTimer = null; };
  const closeSoon = () => {
    if(pinned) return;
    cancelClose();
    closeTimer = setTimeout(() => marker.closePopup(), 250);
  };
  const open = pin => {
    cancelClose();
    if(!card){
      card = navBuildPlaceCard(bld, kind);   // built the first time it is needed (photos load then)
      card.addEventListener('mouseenter', cancelClose);
      card.addEventListener('mouseleave', closeSoon);
      popup.setContent(card);
      marker.bindPopup(popup);
      marker.off('click', marker._openPopup); // we open it ourselves below
    }
    pinned = pinned || pin;
    if(!marker.isPopupOpen()) marker.openPopup();
    navFitPlaceCard(popup, marker, kind);
  };
  marker.on('mouseover', () => open(false));
  marker.on('mouseout', closeSoon);
  marker.on('click', () => open(true));
  marker.on('popupclose', () => { pinned = false; cancelClose(); });
  // keep the card inside the map while the student pans or zooms with it open
  navMap.on('zoomend moveend resize', () => { if(marker.isPopupOpen()) navFitPlaceCard(popup, marker, kind); });
}

let navMap = null, navInit = null, navHome = null;
const navBuildings = new Map();
const navPinMarkers = new Map();   // building id -> its pin on the map
let navUser = null, navAccuracy = null, navRouteLayer = null, navDestMarker = null, navStartMarker = null;
let navDest = null, navStart = null, navEndSnap = null, navWatchId = null, navLast = null;
let navBusy = false, navReqId = 0, navNote = '';

function navMessage(text){
  showRouteMessage(text);
}

function showRouteMessage(text){
  const box = document.getElementById('routeResult');
  if(!box) return;
  box.style.display = 'block';
  box.textContent = text;
}

// Builds the map (limited to the campus area) and fills the From and To lists.
function initNavigator(){
  if(navInit) return navInit;
  navInit = (async () => {
    if(typeof L === 'undefined') throw new Error('The map library (Leaflet) could not be loaded.');
    const mapEl = document.getElementById('campusMap');

    const {res, data: campus} = await api('/api/campus');
    if(!res.ok) throw new Error(campus.error || 'Could not load campus data');
    navHome = campus.home;

    const b = campus.bounds;
    const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);

    // zoom buttons go bottom-right (not top-left, where they covered pins);
    // on touch screens they are hidden by style.css because pinch-zoom is used there
    navMap = L.map(mapEl, { maxZoom: 19, maxBoundsViscosity: 1.0, zoomControl: false });
    L.control.zoom({ position: 'bottomright' }).addTo(navMap);
    // ---- map layers ----
    // Bottom: the OpenStreetMap street map (what the app used before).
    // Top: Esri World Imagery (satellite), loaded ONLY for the area covered by
    // the .osm file (campus.bounds). Routes are still calculated on the server
    // from the .osm footpaths; the route line is simply drawn above both layers.
    const streetLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(navMap);

    const satelliteLayer = L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
        maxZoom: 19,
        maxNativeZoom: 18,   // past 18 Esri may have no photo here; stretch the level-18 tiles instead
        bounds: bounds,      // don't download tiles outside the .osm area
        attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics'
      }).addTo(navMap);

    // Tiles are squares, so they spill a little past the .osm edges. This mask
    // greys out everything outside the exact .osm rectangle, so the satellite
    // photo appears to stop exactly where the campus data stops.
    navMap.createPane('outsideMask');
    navMap.getPane('outsideMask').style.zIndex = 350;          // above tiles (200), below routes (400)
    navMap.getPane('outsideMask').style.pointerEvents = 'none';
    L.polygon([
      [[-90, -180], [-90, 180], [90, 180], [90, -180]],        // the whole world...
      [[b.south, b.west], [b.north, b.west], [b.north, b.east], [b.south, b.east]]  // ...minus the campus
    ], {
      pane: 'outsideMask', stroke: false, fillColor: '#f2f2f2', fillOpacity: 0.85, interactive: false
    }).addTo(navMap);

    // small switch in the top-right corner: tick/untick "Satellite view".
    // The street map always stays underneath.
    L.control.layers(null, { 'Satellite view': satelliteLayer }, { position: 'topright', collapsed: false })
      .addTo(navMap);

    navMap.invalidateSize();
    navMap.fitBounds(bounds.pad(0.05));
    navMap.setMinZoom(navMap.getZoom());       // can't zoom out past the campus
    navMap.setMaxBounds(bounds.pad(0.25));     // can't pan away from the campus
    navMap.on('zoomend', navUpdatePinVisibility);

    const toSelect = document.getElementById('toSelect');
    const fromSelect = document.getElementById('fromSelect');
    toSelect.replaceChildren(new Option('Select destination...', ''));
    fromSelect.replaceChildren(new Option('Current location', 'current'));

    campus.buildings.forEach(bld => {
      navBuildings.set(bld.id, bld);
      const extra = bld.description && bld.description.toLowerCase() !== bld.name.toLowerCase()
        ? ` (${bld.description})` : '';
      toSelect.add(new Option(bld.name + extra, bld.id));
      fromSelect.add(new Option(bld.name + extra, bld.id));

      // a pin icon that matches the kind of place (building, clinic, gate, residence...)
      const kind = navPlaceKind(bld);
      const marker = L.marker([bld.lat, bld.lng], {
        icon: navPinIcon(kind),
        title: bld.name,
        alt: bld.name,
        riseOnHover: true
      }).addTo(navMap);
      navPinMarkers.set(bld.id, marker);
      navAttachPlaceCard(marker, bld, kind);
    });

    navUpdatePinVisibility();

    if(navSimulate){
      // Desk testing: click the map to "walk" there
      navMap.on('click', e => navHandlePosition(
        { coords: { latitude: e.latlng.lat, longitude: e.latlng.lng, accuracy: 5 } }, true));
    }
  })();
  navInit.catch(() => { navInit = null; });
  return navInit;
}

// The map sits in a view that starts hidden, so build it (and fix its size) when the view opens.
function onNavigatorShown(){
  initNavigator()
    .then(() => setTimeout(() => navMap.invalidateSize(), 50))
    .catch(err => {
      console.error(err);
      if(err.message !== 'signed out') navMessage('The map could not be loaded. Please refresh the page and try again.');
    });
}

function navDrawUser(lat, lng, accuracy){
  const ll = [lat, lng];
  if(!navUser){
    navAccuracy = L.circle(ll, { radius: accuracy, weight: 0, fillColor: NAV_COLORS.user, fillOpacity: 0.12 }).addTo(navMap);
    navUser = L.circleMarker(ll, {
      radius: 8, color: '#fff', weight: 3, fillColor: NAV_COLORS.user, fillOpacity: 1, bubblingMouseEvents: false
    }).addTo(navMap);
    navUser.bindTooltip('You are here');
  } else {
    navUser.setLatLng(ll);
    navAccuracy.setLatLng(ll).setRadius(accuracy);
  }
}

function navClearRoute(){
  if(navRouteLayer){ navRouteLayer.remove(); navRouteLayer = null; }
  if(navDestMarker){
    const el = navDestMarker.getElement();
    if(el) el.classList.remove('pin-is-destination');
    navDestMarker.setZIndexOffset(0);
    navDestMarker = null;
  }
  if(navStartMarker){ navStartMarker.remove(); navStartMarker = null; }
}

function navStopTracking(){
  if(navWatchId !== null){ navigator.geolocation.clearWatch(navWatchId); navWatchId = null; }
}

function setStopButton(visible){
  document.getElementById('stopRouteBtn').hidden = !visible;
}

function navDrawRoute(data, fit){
  if(navRouteLayer) navRouteLayer.remove();
  const color = NAV_COLORS.route;
  navRouteLayer = L.layerGroup([
    // white outline under the route so it stands out on the satellite photo
    L.polyline(data.coordinates, { color: '#ffffff', weight: 10, opacity: 0.9, interactive: false }),
    L.polyline(data.start_connector, { color, weight: 3, dashArray: '6 8', opacity: 0.8 }),
    L.polyline(data.coordinates, { color, weight: 6, opacity: 0.9 }),
    L.polyline(data.end_connector, { color, weight: 3, dashArray: '6 8', opacity: 0.8 })
  ]).addTo(navMap);

  navEndSnap = data.end_connector[0];

  if(fit){
    navMap.fitBounds(
      L.latLngBounds([...data.coordinates, ...data.start_connector, ...data.end_connector]),
      { padding: [40, 40], maxZoom: 18 }
    );
  }

  const minutes = Math.max(1, Math.round(data.estimated_walk_time_min));
  let msg = `${navDest.name}: ${Math.round(data.distance_meters)} m · about ${minutes} min walk`;
  if(navStart) msg = `From ${navStart.name} to ${msg}`;
  else if(data.off_campus) msg += ` (you seem to be off campus, so this route starts at the ${data.start_name})`;
  else if(data.start_name !== 'your location') msg += ` (starting from the ${data.start_name})`;
  if(navNote) msg += ` ${navNote}`;
  navMessage(msg);
}

// Ask the server for a route: from a chosen building (navStart), from the
// student's position (pos), or from the main gate when neither is available.
async function navRequestRoute(pos, force){
  if(!navDest || (navBusy && !force)) return;
  const myId = ++navReqId;
  navBusy = true;
  try{
    const body = { destination_id: navDest.id };
    if(navStart) body.start_id = navStart.id;
    else if(pos) body.start = { lat: pos.lat, lng: pos.lng };
    const {res, data} = await api('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    if(myId !== navReqId || !navDest) return;   // destination changed or route ended meanwhile
    if(!res.ok){ navMessage(data.error || 'Could not calculate a route.'); return; }
    navDrawRoute(data, force);
  } catch(err){
    console.error(err);
    if(err.message !== 'signed out') navMessage('Could not reach the navigation server. Please try again.');
  } finally {
    navBusy = false;
  }
}

function navArrived(){
  const name = navDest ? navDest.name : 'your destination';
  navReqId++;                 // discard any route request still in flight
  navStopTracking();
  navClearRoute();
  navDest = null;
  navEndSnap = null;
  setStopButton(false);
  navMessage(`You have arrived at ${name}.`);
}

// "End route" button: stop following the student's location and clear the route
function navEndRoute(){
  navReqId++;
  navStopTracking();
  navClearRoute();
  navDest = null;
  navStart = null;
  navEndSnap = null;
  setStopButton(false);
  navMessage('Route ended.');
}
document.getElementById('stopRouteBtn').addEventListener('click', navEndRoute);

// Runs for the first GPS fix (force = true) and then for every watchPosition update.
function navHandlePosition(pos, force){
  const { latitude: lat, longitude: lng, accuracy } = pos.coords;
  navDrawUser(lat, lng, accuracy);
  if(!navDest || navStart) return;

  const nearBuilding = navMap.distance([lat, lng], [navDest.lat, navDest.lng]) <= NAV_ARRIVE_M;
  const nearPathEnd = navEndSnap && navMap.distance([lat, lng], navEndSnap) <= NAV_ARRIVE_M;
  if(nearBuilding || nearPathEnd){ navArrived(); return; }

  const now = Date.now();
  if(!force){
    if(accuracy > NAV_MAX_ACCURACY_M) return;
    if(navLast && (
        now - navLast.t < NAV_MIN_INTERVAL_MS ||
        navMap.distance([lat, lng], [navLast.lat, navLast.lng]) < NAV_MIN_MOVE_M)) return;
  }
  navLast = { t: now, lat, lng };
  navRequestRoute({ lat, lng }, force);
}

function navGeoReason(err){
  if(!window.isSecureContext) return 'Live location only works on https:// pages (or localhost).';
  if(err.code === 1) return 'Location access was blocked. Allow location for this site in your browser settings, or choose a starting building under "From".';
  if(err.code === 2) return 'Your location is not available right now.';
  return 'Getting your location took too long.';
}

// "Get Route" button / tapping a building on the map
async function getRoute(){
  const toSelect = document.getElementById('toSelect');
  const fromValue = document.getElementById('fromSelect').value;
  if(!toSelect.value){
    navMessage('Please select a destination.');
    return;
  }
  try{
    await initNavigator();
  } catch(err){
    console.error(err);
    if(err.message !== 'signed out') navMessage('The map could not be loaded. Please refresh the page and try again.');
    return;
  }
  const dest = navBuildings.get(toSelect.value);
  if(!dest) return;
  if(fromValue === dest.id){
    navMessage(`You are already at ${dest.name}. Choose a different destination.`);
    return;
  }

  navStopTracking();
  navClearRoute();
  navDest = dest;
  navStart = fromValue === 'current' ? null : (navBuildings.get(fromValue) || null);
  navEndSnap = null;
  navLast = null;
  navNote = '';
  // highlight the destination's own pin (instead of drawing a ring under it)
  navDestMarker = navPinMarkers.get(dest.id) || null;
  if(navDestMarker){
    const el = navDestMarker.getElement();
    if(el) el.classList.add('pin-is-destination');
    navDestMarker.setZIndexOffset(1000);
  }
  setStopButton(true);
  navMap.invalidateSize();

  // From a chosen building: one route, nothing to follow
  if(navStart){
    navStartMarker = L.circleMarker([navStart.lat, navStart.lng], {
      radius: 9, color: '#fff', weight: 3, fillColor: NAV_COLORS.route, fillOpacity: 1, bubblingMouseEvents: false
    }).addTo(navMap);
    navRequestRoute(null, true);
    return;
  }

  // From the current location: follow the student and update the route as they walk
  if(navSimulate){
    navMessage('Simulation: starting at the main gate. Click the map to move.');
    navRequestRoute(null, true);
    return;
  }
  if(!('geolocation' in navigator)){
    navNote = 'This browser cannot share your location, so the route starts at the main gate.';
    navRequestRoute(null, true);
    return;
  }

  navMessage('Finding your current location...');
  navigator.geolocation.getCurrentPosition(
    pos => {
      navHandlePosition(pos, true);   // first fix: initial position + route
      navWatchId = navigator.geolocation.watchPosition(
        p => navHandlePosition(p, false),
        err => { if(err.code === 1) navMessage(navGeoReason(err)); },
        { enableHighAccuracy: true, maximumAge: 2000, timeout: 20000 }
      );
    },
    err => {
      navNote = navGeoReason(err);
      navRequestRoute(null, true);    // still give them a route, from the main gate
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 0 }
  );
}