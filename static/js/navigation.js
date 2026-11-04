// Campus Navigator: the Leaflet map, From/To, live GPS.

// Campus Navigator: Leaflet map + live GPS + Flask /api/route
// The routes come from the server (campus_graph.py builds the footpath and road network from
// the campus OpenStreetMap export).
const NAV_ARRIVE_M = 20;           // this close to the building = arrived
const NAV_ARRIVE_PATH_END_M = 10;  // or this close to where the path ends at the building
const NAV_ROUGH_SHOW_M = 150;      // near the destination, rough readings up to this still move the arrow
const NAV_AFTER_ARRIVE_MS = 5 * 60 * 1000;   // the live arrow keeps going this long after arriving
const NAV_MAX_ACCURACY_M = 100;    // ignore live GPS updates that are less accurate than this
const NAV_PICKED_ID = '__map__';   // value of the "Point I set on the map" choice under From
const NAV_GOOD_ACCURACY_M = 30;    // a first fix this accurate starts the route straight away...
const NAV_FIX_WAIT_MS = 20000;     // ...otherwise wait this long after the FIRST reading for a better one
const NAV_NO_FIX_MS = 45000;       // no reading at all after this long (permission prompt included): give up
// desk testing: open /?simulate=1#navigate and click the map to "walk" there
const navSimulate = new URLSearchParams(location.search).has('simulate');

const NAV_COLORS = {
  route: '#1d5fd1',        // route line
  building: '#0b3d91',     // building markers (UNIZULU blue)
  user: '#1a73e8',         // "you are here" dot
  destination: '#c63c3c',  // ring around the destination
};

// Styles for the map pins, place cards, fading and zoom hint
// They live here (not in style.css) so this one file carries the whole feature:
// replacing navigation.js is enough, even if an older style.css is still cached.
const NAV_VERSION = '2026-11-02e';
const NAV_STYLES = `
/* Campus map pins (navigation.js picks one per place) Each pin is a rotated square with one sharp corner = a map-pin teardrop. */
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
/* parking P markers follow the same rule (the parking of the current route stays) */
.nav-lot-icon{ transition: opacity .35s ease; }
#campusMap.pins-faded .nav-lot-icon:not(.lot-on-route){ opacity: 0; pointer-events: none; }
.zoom-hint{
  position:absolute; left:50%; bottom: 14px; transform: translateX(-50%);
  margin:0; z-index: 500;
  background: rgba(14,30,60,.78); color:#fff;
  font-size: 12.5px; font-weight: 600;
  padding: 6px 14px; border-radius: 16px;
  pointer-events: none; white-space: nowrap;
}

/* Place card (tooltip.html) — opens from a map pin */
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


/* Walk / Car + walk / Car only switch */
.mode-toggle{
  display:flex; flex-shrink:0; gap:2px; padding:3px;
  background: var(--bg); border:1px solid var(--border); border-radius:22px;
}
.mode-toggle button{
  border:none; background:none; border-radius:18px;
  padding:8px 14px; font: inherit; font-size:13px; font-weight:600;
  color: var(--text-mid); cursor:pointer; white-space:nowrap;
  transition: background-color .15s ease, color .15s ease;
}
.mode-toggle button:hover{ color: var(--text); }
.mode-toggle button.active{ background: var(--btn); color:#fff; }
@media (max-width: 900px){ .mode-toggle{ flex-basis:100%; } .mode-toggle button{ flex:1; } }

/* "Alternate route" button (map header) */
.alt-route-btn{
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: var(--blue); font-size: 12.5px; font-weight: 600; padding: 5px 10px; cursor: pointer;
}
.alt-route-btn:hover{ border-color: var(--blue); background: var(--blue-soft); }

/* route list in the side card */
.route-choices{ display:flex; flex-direction:column; gap:8px; margin-top:12px; text-align:left; }
.route-choices[hidden]{ display:none; }
.route-choice{
  display:flex; flex-direction:column; gap:3px; width:100%;
  border:1px solid var(--border); background: var(--bg); color: var(--text);
  border-radius:10px; padding:9px 12px; cursor:pointer; font: inherit; text-align:left;
}
.route-choice:hover{ border-color: var(--blue); }
.route-choice.active{ border-color: var(--blue); background: var(--blue-soft); box-shadow: inset 3px 0 0 var(--blue); }
.rc-top{ display:flex; justify-content:space-between; gap:8px; font-size:13px; }
.rc-time{ font-weight:600; color: var(--blue); }
.rc-how{ font-size:12px; color: var(--text-dim); }
.rc-note{ font-size:12px; color: var(--text-dim); margin:0; }

/* the navigation arrow ("you are here"), turned to the direction of travel */
.nav-user-icon{ background:none; border:none; }
.nav-user-icon .nav-arrow{
  position:absolute; inset:5px;
  transition: transform .45s ease;
  filter: drop-shadow(0 2px 3px rgba(0,0,0,.4));
}
.nav-user-icon .nav-arrow svg{ width:100%; height:100%; display:block; }
.nav-user-pulse{
  position:absolute; inset:7px; border-radius:50%;
  background: rgba(37,43,100,.28);
  animation: navPulse 2s ease-out infinite;
}
@keyframes navPulse{ from{ transform: scale(.6); opacity:.9; } to{ transform: scale(1.7); opacity:0; } }

/* parking area of a car route */
.nav-park-icon{ background:none; border:none; }
.nav-park-icon span{
  display:flex; align-items:center; justify-content:center;
  width:26px; height:26px; border-radius:6px; box-sizing:border-box;
  background:#0b3d91; color:#fff; border:2px solid #fff;
  font: 700 15px/1 Arial, sans-serif; box-shadow: 0 2px 5px rgba(0,0,0,.3);
}

/* wrong way: red banner over the map */
.wrong-way{
  position:absolute; top:12px; left:12px; z-index:1000;
  max-width: calc(100% - 190px);
  display:flex; align-items:center; gap:10px;
  background:#d93025; color:#fff;
  border-radius:12px; padding:10px 12px;
  font-size:13px; font-weight:600; line-height:1.35;
  box-shadow: 0 6px 16px rgba(0,0,0,.3);
  animation: wwIn .35s ease;
}
.wrong-way[hidden]{ display:none; }
.wrong-way .ww-icon{ font-size:20px; }
.wrong-way button{
  flex-shrink:0; background:#fff; color:#d93025;
  border:none; border-radius:8px; padding:6px 10px;
  font: inherit; font-size:12.5px; font-weight:700; cursor:pointer; white-space:nowrap;
}
@media (max-width: 600px){
  .wrong-way{ top:auto; bottom:12px; left:12px; right:12px; max-width:none; flex-wrap:wrap; }
}
@keyframes wwIn{ 0%{ transform: translateX(-6px); opacity:0; } 40%{ transform: translateX(5px); opacity:1; } 70%{ transform: translateX(-3px); } 100%{ transform:none; } }


/* no exact location: ask the student to set it by hand */
.need-loc{
  position:absolute; top:12px; left:12px; z-index:1000;
  max-width: calc(100% - 190px);
  display:flex; align-items:center; gap:10px;
  background:#fff8e1; color:#5d4200; border:1px solid #f0c75e;
  border-radius:12px; padding:10px 12px;
  font-size:13px; font-weight:600; line-height:1.35;
  box-shadow: 0 6px 16px rgba(0,0,0,.2);
  animation: wwIn .35s ease;
}
.need-loc[hidden]{ display:none; }
.need-loc .nl-icon{ font-size:20px; }
.need-loc button{
  flex-shrink:0; background: var(--btn); color:#fff;
  border:none; border-radius:8px; padding:7px 11px;
  font: inherit; font-size:12.5px; font-weight:700; cursor:pointer; white-space:nowrap;
}
@media (max-width: 600px){
  .need-loc{ top:auto; bottom:12px; left:12px; right:12px; max-width:none; flex-wrap:wrap; }
}
.set-loc-btn.attention{ animation: navAttention 1.2s ease-in-out infinite; border-color:#f0a020; }
@keyframes navAttention{ 0%,100%{ box-shadow: 0 0 0 0 rgba(240,160,32,.6); } 50%{ box-shadow: 0 0 0 6px rgba(240,160,32,0); } }

/* "Road closed" button and closed stretches */
.closed-btn{
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: #b06000; font-size: 12.5px; font-weight: 600; padding: 5px 10px; cursor: pointer;
}
.closed-btn:hover{ border-color: #f0a020; background: rgba(240,160,32,.08); }
.nav-closed-icon{ background:none; border:none; font-size:18px; line-height:26px; text-align:center;
  filter: drop-shadow(0 1px 2px rgba(0,0,0,.4)); }

/* buildings passed on the way (side card) */
.landmark-feed{ list-style:none; margin:12px 0 0; padding:0; text-align:left; display:flex; flex-direction:column; gap:4px; }
.landmark-feed[hidden]{ display:none; }
.landmark-feed li{ font-size:12.5px; color: var(--text-mid); padding:6px 10px; border-radius:8px; background: var(--surface); }
.landmark-feed li:first-child{ color: var(--text); font-weight:600; }

/* short messages over the map ("Park here", "Back on the route") */
.nav-toast{
  position:absolute; left:50%; bottom: 48px; transform: translateX(-50%);
  z-index: 1001; margin:0;
  background: rgba(14,30,60,.9); color:#fff;
  font-size: 13px; font-weight: 600; text-align:center;
  padding: 8px 14px; border-radius: 16px;
  max-width: calc(100% - 24px); pointer-events:none;
}
.nav-toast[hidden]{ display:none; }

/* Car only / parking status */
/* "Parking full" button (map header, shown on car + walk routes) */
.park-full-btn{
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: #c5221f; font-size: 12.5px; font-weight: 600; padding: 5px 10px; cursor: pointer;
}
.park-full-btn:hover{ border-color: #d93025; background: rgba(217,48,37,.08); }
.park-full-btn[hidden]{ display:none; }
.parked-btn{
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  color: #188038; font-size: 12.5px; font-weight: 600; padding: 5px 10px; cursor: pointer;
}
.parked-btn:hover{ border-color: #188038; background: rgba(24,128,56,.08); }
.parked-btn[hidden]{ display:none; }
/* parkings this driver already tried on this trip */
.nav-lot.tried{ opacity:.75; }
.nav-lot.tried::before{
  content:'✕'; position:absolute; top:-7px; right:-7px; width:14px; height:14px; border-radius:50%;
  background:#fff; color:#d93025; font: 700 10px/14px Arial, sans-serif; text-align:center;
  box-shadow: 0 1px 3px rgba(0,0,0,.35);
}

/* little line samples used in the help text and the legend */
.leg-key{ display:inline-block; width:26px; height:6px; border-radius:3px; vertical-align:middle;
  box-shadow: 0 0 0 2px #fff, 0 0 0 3px rgba(0,0,0,.12); }
.leg-key-drive{ background:#0b3d91; height:8px; }
.leg-key-walk{ background:#16a34a; }

/* legend in the corner of the map: what the colours of THIS route mean */
.nav-legend{
  background: rgba(255,255,255,.95); color:#202124;
  border-radius:10px; padding:7px 10px; box-shadow: 0 2px 8px rgba(0,0,0,.2);
  font: 600 12px/1.2 Poppins, Arial, sans-serif; display:flex; flex-direction:column; gap:6px;
}
.nav-legend[hidden]{ display:none; }
.nav-legend .lg-row{ display:flex; align-items:center; gap:8px; white-space:nowrap; }
@media (max-width: 600px){
  .nav-legend{ font-size:10.5px; padding:5px 7px; gap:4px; margin-bottom:22px !important; }
  .nav-legend .leg-key{ width:18px; }
}
.nav-legend .lg-p{ display:inline-flex; width:16px; height:16px; border-radius:4px; color:#fff;
  align-items:center; justify-content:center; font:700 11px/1 Arial, sans-serif; }

/* 🚗 / 👣 badge in the middle of each part of the route */
.nav-leg-badge{ background:none; border:none; }
.nav-leg-badge span{
  display:flex; align-items:center; justify-content:center;
  width:26px; height:26px; border-radius:50%; box-sizing:border-box;
  border:2px solid #fff; font-size:14px; line-height:1;
  box-shadow: 0 2px 5px rgba(0,0,0,.35);
}
.nav-leg-badge.drive span{ background:#0b3d91; }
.nav-leg-badge.walk span{ background:#16a34a; }

/* drop-off point of a car-only route */
.nav-drop-icon{ background:none; border:none; }
.nav-drop-icon span{
  display:flex; align-items:center; justify-content:center; gap:3px;
  height:24px; padding:0 8px; border-radius:12px; white-space:nowrap;
  background:#0b3d91; color:#fff; border:2px solid #fff; box-sizing:border-box;
  font: 700 11px/1 Poppins, Arial, sans-serif; box-shadow: 0 2px 5px rgba(0,0,0,.3);
}

/* every parking area on the map: red = reported full by a driver */
.nav-lot-icon{ background:none; border:none; }
.nav-lot{
  position:relative; display:flex; align-items:center; justify-content:center;
  width:24px; height:24px; border-radius:6px; box-sizing:border-box;
  color:#fff; border:2px solid #fff; font: 700 13px/1 Arial, sans-serif;
  box-shadow: 0 2px 5px rgba(0,0,0,.3); cursor:pointer;
}
.nav-lot.free{ background:#188038; }
.nav-lot.full{ background:#d93025; }
.nav-lot.full::after{
  content:'FULL'; position:absolute; top:100%; left:50%; transform:translate(-50%, 2px);
  background:#d93025; color:#fff; border-radius:4px; padding:1px 4px; font: 700 8.5px/1.2 Arial, sans-serif;
}
.nav-lot.on-route{ width:30px; height:30px; font-size:16px; border-width:3px;
  box-shadow: 0 0 0 3px rgba(11,61,145,.55), 0 3px 8px rgba(0,0,0,.35); }
.lot-popup .leaflet-popup-content{ margin:10px 12px; font-family: Poppins, Arial, sans-serif; }
.lot-card h4{ margin:0 0 4px; font-size:14px; color:#202124; }
.lot-card .lot-state{ margin:0 0 8px; font-size:12.5px; font-weight:600; }
.lot-card .lot-state.free{ color:#5f6368; }
.lot-card .lot-state.full{ color:#d93025; }
.lot-card .lot-actions{ display:flex; gap:6px; flex-wrap:wrap; }
.lot-card button{
  border:none; border-radius:14px; padding:6px 10px; cursor:pointer;
  font: 600 12px/1 Poppins, Arial, sans-serif; color:#fff;
}
.lot-card .lot-full{ background:#d93025; }
.lot-card .lot-free{ background:#188038; }

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

// Map pin icons (instead of plain dots)
// Which icon each place gets is decided from its name (case does not matter).
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

// Place card (shown when a pin is hovered or tapped)
// Photos, accessibility and opening hours come from buildings.json when a place has them:
// "images": ["img/places/library-1.jpg"
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

// Places the card so it is never cut off by the edge of the map: above the pin when there is
// room, otherwise below it
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
    hint.textContent = 'Zoom in to see buildings and parking';
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

let navMap = null, navInit = null, navHome = null, navCampusBox = null;
const navBuildings = new Map();
const navPinMarkers = new Map();   // building id -> its pin on the map
let navUser = null, navAccuracy = null, navRouteLayer = null, navDestMarker = null, navStartMarker = null;
let navDest = null, navStart = null, navEndSnap = null, navWatchId = null;
let navBusy = false, navReqId = 0, navNote = '', navFixTimer = null;
let navLastDest = null, navAfterArriveTimer = null;   // after arriving: where, and when live location stops
let navPicking = false, navPicked = null;   // "Set my location": the point the student placed by hand

// travel mode, alternatives, live following
// "walk" = footsteps all the way.  "car" = drive on the roads to a parking area, then walk.
// "drive" = car only: drive on the roads to the drop-off point nearest the building.
const NAV_MODES = ['walk', 'car', 'drive'];
let navMode = 'walk';
try{ const m = localStorage.getItem('navMode'); if(NAV_MODES.includes(m)) navMode = m; } catch(e){}
let navRoutes = [], navRouteIdx = 0, navReply = null;   // routes from the server; [0] = shortest
let navTrack = null;        // the chosen route as one line, for following the student along it
let navFollow = null;       // progress along navTrack (see navFollowPosition)
let navLastPos = null;      // last position seen (for the arrow's direction and "Re-route from here")
let navRouteLines = [];     // the coloured route lines (they turn red when going the wrong way)
let navDoneLine = null;     // grey line over the part already walked/driven
let navParkMarker = null;
let navLegend = null;       // map legend: which colour is driven / walked

// parking areas and their status (reported full or not)
let navLots = [];                  // [{id, name, lat, lng, full, ...}] from /api/parking
const navLotMarkers = new Map();   // parking id -> P marker
let navLotLayer = null;
let navSkipParking = [];           // parkings THIS driver found full on this trip (never offered again)
let navParkedAt = null;            // the parking where the driver confirmed "✓ Parked here"
let navParkingMinutes = 45;        // how long a "full" report counts (from the server)
const NAV_LOTS_REFRESH_MS = 60000;
const NAV_DRIVE_M_PER_MIN = 333;   // same speed as the server (campus_graph.py)

const NAV_OFF_ROUTE_WALK_M = 25;   // further than this from the route (walking) = off the route
const NAV_OFF_ROUTE_DRIVE_M = 35;  // ...when driving (roads are wider, GPS jumps more at speed)
const NAV_BACKWARDS_M = 15;        // moved this far back along the route = going the wrong way
const NAV_BAD_FIXES = 2;           // this many bad readings in a row before warning (GPS jitter)

// changing path / closed roads: automatic re-routing from where the student is
const NAV_AUTO_REROUTE_FIXES = 4;  // off the route for this many readings in a row (~4 s) -> new route from here
const NAV_TURNED_BACK_M = 50;      // went this far back along the route -> they changed their mind / way is closed
const NAV_REROUTE_COOLDOWN_MS = 8000;
const NAV_AVOID_AHEAD_M = 70;      // the stretch after the turning point that is treated as closed
let navAvoid = [];                 // closed / abandoned points sent to the server (this trip only)
let navClosedLayer = null;         // grey "closed" stretches on the map
let navLastReroute = 0;

// "You're near ... / You've passed ..."
const NAV_LANDMARK_NEAR_M = 35;    // this close to a building = "you're just near it"
const NAV_LANDMARK_PASS_M = 50;    // then this far away again = "you've passed it"
const navLandmarks = new Map();    // building id -> 'near' | 'passed' | 'skip'
let navLandmarksPrimed = false;

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
    navCampusBox = campus.bounds;

    const b = campus.bounds;
    const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);

    // zoom buttons go bottom-right (not top-left, where they covered pins);
    // on touch screens they are hidden by style.css because pinch-zoom is used there
    navMap = L.map(mapEl, { maxZoom: 19, maxBoundsViscosity: 1.0, zoomControl: false });
    L.control.zoom({ position: 'bottomright' }).addTo(navMap);
    // map layers
    // Bottom: the OpenStreetMap street map (what the app used before).
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

    // Tiles are squares, so they spill a little past the .osm edges.
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

    // the navigation arrow sits above the pins and the route
    navMap.createPane('navUserPane');
    navMap.getPane('navUserPane').style.zIndex = 650;

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

    // parking areas (P markers, red = reported full; shown after 3 zoom-ins) + the route legend
    navParkingMinutes = campus.parking_full_minutes || navParkingMinutes;
    navSetLots(campus.parkings || []);
    navLegend = L.control({ position: 'bottomleft' });
    navLegend.onAdd = () => {
      const box = navEl('div', 'nav-legend');
      box.hidden = true;
      L.DomEvent.disableClickPropagation(box);
      return box;
    };
    navLegend.addTo(navMap);
    setInterval(navRefreshLots, NAV_LOTS_REFRESH_MS);   // other drivers' reports show up by themselves

    navMap.on('click', e => {
      if(navPicking){ navPickLocation(e.latlng); return; }
      if(navSimulate){
        // Desk testing: click the map to "walk" (or drive) there
        navHandlePosition({ coords: { latitude: e.latlng.lat, longitude: e.latlng.lng, accuracy: 5 } }, false);
      }
    });
  })();
  navInit.catch(() => { navInit = null; });
  return navInit;
}

// The map sits in a view that starts hidden, so build it (and fix its size) when the view opens.
function onNavigatorShown(){
  navSyncModeButtons();
  initNavigator()
    .then(() => setTimeout(() => navMap.invalidateSize(), 50))
    .catch(err => {
      console.error(err);
      if(err.message !== 'signed out') navMessage('The map could not be loaded. Please refresh the page and try again.');
    });
}

// The "you are here" navigation arrow. It points the way the student is
// heading and glides to each new position, so it moves along with them.
const NAV_ARROW_SVG =
  '<svg viewBox="0 0 40 40" aria-hidden="true">' +
  '<path d="M20 3 L35 35 L20 27 L5 35 Z" fill="#252b64" stroke="#ffffff" stroke-width="3" stroke-linejoin="round"/>' +
  '</svg>';
let navHeading = 0;

function navArrowIcon(){
  return L.divIcon({
    className: 'nav-user-icon',
    html: `<div class="nav-user-pulse"></div><div class="nav-arrow">${NAV_ARROW_SVG}</div>`,
    iconSize: [44, 44],
    iconAnchor: [22, 22],
  });
}

function navSetHeading(deg){
  if(deg === null || deg === undefined || isNaN(deg)) return;
  // turn the short way round (e.g. 350 -> 10 turns 20 degrees, not 340)
  let diff = ((deg - navHeading) % 360 + 540) % 360 - 180;
  navHeading += diff;
  const el = navUser && navUser.getElement();
  const arrow = el && el.querySelector('.nav-arrow');
  if(arrow) arrow.style.transform = `rotate(${navHeading}deg)`;
}

function navBearing(a, b){
  const toRad = d => d * Math.PI / 180;
  const y = Math.sin(toRad(b[1] - a[1])) * Math.cos(toRad(b[0]));
  const x = Math.cos(toRad(a[0])) * Math.sin(toRad(b[0])) -
            Math.sin(toRad(a[0])) * Math.cos(toRad(b[0])) * Math.cos(toRad(b[1] - a[1]));
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

// The arrow moves smoothly with your speed: between GPS readings it keeps going at the current
// speed and direction (along the route when you are on it, so it follows the bends), and each
// new reading corrects it gently instead of making it jump or wait.
const NAV_PREDICT_MAX_S = 3;      // keep moving on our own for at most this long after a reading
const NAV_MIN_SPEED = 0.6;        // slower than this (m/s) counts as standing still
const NAV_MAX_SPEED = 40;         // faster than this is a GPS jump, not real movement
const NAV_SMOOTH_S = 0.35;        // how quickly the arrow catches up with where it should be
let navMotion = null;             // the last reading: position, time, speed, direction, place on the route
let navAnim = null, navAnimLast = 0;

function navUpdateMotion(lat, lng, accuracy, info){
  const t = performance.now() / 1000, prev = navMotion;
  const gpsSpeed = info && typeof info.speed === 'number' && info.speed >= 0 ? info.speed : null;
  const gpsHeading = info && typeof info.heading === 'number' && !isNaN(info.heading) ? info.heading : null;
  // on the route: how far along it we are, and which way we are going
  let along = null, dir = 1;
  const sameTrack = prev && prev.track === navTrack && prev.along !== null;
  if(navTrack && navFollow){
    // nearest place on the route, picking the one closest to where we expected to be
    const p = navProject(lat, lng, sameTrack ? prev.along + prev.dir * prev.speed * (t - prev.t) : undefined, 3);
    // close enough to the path for the reading's accuracy: the arrow sits on the path line
    if(p && p.dist <= Math.min(Math.max(accuracy || 0, 8), 20)){
      along = p.along;
      if(sameTrack && along < prev.along - 2) dir = -1;
    }
  }
  let speed = gpsSpeed || 0, heading = gpsHeading;
  const recent = (prev ? prev.recent : []).filter(r => t - r.t <= 4 && r.track === navTrack);
  recent.push({ lat, lng, t, along, track: navTrack });
  if(prev){
    if(gpsSpeed === null){
      // the device gave no speed: work it out from the readings of the last few seconds
      // (on the route: from the distance moved along the path), so a small GPS wobble
      // while standing does not count as moving
      const o = recent[0], dt = t - o.t;
      const onPath = o.along !== null && along !== null;
      const d = onPath ? Math.abs(along - o.along) : navMap.distance([o.lat, o.lng], [lat, lng]);
      const wobble = Math.max(onPath ? 3 : 1.5, Math.min(accuracy || 0, 30) * 0.4);
      speed = dt > 0.5 && d >= wobble ? d / dt : 0;
      if(speed > 0) heading = navBearing([o.lat, o.lng], [lat, lng]);
    }
    if(heading === null && speed > 0) heading = prev.heading;
    speed = 0.6 * speed + 0.4 * prev.speed;      // smooth out noisy speeds
  }
  if(speed < NAV_MIN_SPEED || speed > NAV_MAX_SPEED || (accuracy || 0) > NAV_MAX_ACCURACY_M || navSimulate) speed = 0;
  navMotion = { lat, lng, t, speed, heading, along, dir, track: navTrack, recent };
}

function navPointAlong(d){
  const C = navTrack.cum;
  let i = 0;
  while(i < C.length - 2 && C[i + 1] < d) i++;
  const seg = C[i + 1] - C[i];
  return navPointAt(i, seg ? Math.max(0, Math.min(1, (d - C[i]) / seg)) : 0);
}

// where the arrow should be at time "when" (seconds), from the last reading and the speed
function navPredicted(when){
  const m = navMotion;
  const dt = Math.min(Math.max(0, when - m.t), NAV_PREDICT_MAX_S);
  const s = m.speed * dt;
  if(m.along !== null && m.track && m.track === navTrack){
    return navPointAlong(Math.max(0, Math.min(navTrack.total, m.along + m.dir * s)));
  }
  if(!s || m.heading === null) return [m.lat, m.lng];
  const rad = m.heading * Math.PI / 180;
  return [m.lat + s * Math.cos(rad) / 110540, m.lng + s * Math.sin(rad) / (111320 * Math.cos(m.lat * Math.PI / 180))];
}

function navAnimate(ts){
  navAnim = null;
  if(!navUser || !navMotion) return;
  const now = ts / 1000, dt = navAnimLast ? Math.min(0.1, now - navAnimLast) : 0.016;
  navAnimLast = now;
  // aim a little ahead, so the smoothing does not leave the arrow behind
  const target = navPredicted(performance.now() / 1000 + NAV_SMOOTH_S);
  const cur = navUser.getLatLng(), k = 1 - Math.exp(-dt / NAV_SMOOTH_S);
  const next = L.latLng(cur.lat + (target[0] - cur.lat) * k, cur.lng + (target[1] - cur.lng) * k);
  navUser.setLatLng(next);
  navAccuracy.setLatLng(next);
  const predicting = navMotion.speed > 0 && performance.now() / 1000 - navMotion.t < NAV_PREDICT_MAX_S;
  if(predicting || navMap.distance(next, target) > 0.3) navAnim = requestAnimationFrame(navAnimate);
  else navAnimLast = 0;
}

function navDrawUser(lat, lng, accuracy, info){
  const ll = L.latLng(lat, lng);
  if(!navUser){
    navAccuracy = L.circle(ll, { radius: accuracy, weight: 0, fillColor: NAV_COLORS.user, fillOpacity: 0.12, interactive: false }).addTo(navMap);
    navUser = L.marker(ll, { icon: navArrowIcon(), pane: 'navUserPane', keyboard: false, bubblingMouseEvents: false }).addTo(navMap);
    navUser.bindTooltip('You are here');
    navSetHeading(navHeading);
    navMotion = null;
    navUpdateMotion(lat, lng, accuracy, info);
    return;
  }
  navAccuracy.setRadius(accuracy);
  if(navMap.distance(navUser.getLatLng(), ll) > 300){
    // big jump: straight there, and do not count it as speed
    navUser.setLatLng(ll); navAccuracy.setLatLng(ll);
    navMotion = null;
    navUpdateMotion(lat, lng, accuracy, info);
    return;
  }
  navUpdateMotion(lat, lng, accuracy, info);
  if(!navAnim) navAnim = requestAnimationFrame(navAnimate);
}

function navClearRoute(){
  if(navRouteLayer){ navRouteLayer.remove(); navRouteLayer = null; }
  if(navParkMarker){ navParkMarker.remove(); navParkMarker = null; }
  navShowLegend(null);
  ['parkFullBtn', 'parkedBtn'].forEach(id => { const b = document.getElementById(id); if(b) b.hidden = true; });
  navRouteLines = [];
  navDoneLine = null;
  navTrack = null;
  navFollow = null;
  navShowWrongWay(false, '', true);
  if(navDestMarker){
    const el = navDestMarker.getElement();
    if(el) el.classList.remove('pin-is-destination');
    navDestMarker.setZIndexOffset(0);
    navDestMarker = null;
  }
  if(navStartMarker){ navStartMarker.remove(); navStartMarker = null; }
}

function navStopTracking(){
  if(navFixTimer !== null){ clearTimeout(navFixTimer); navFixTimer = null; }
  if(navAfterArriveTimer !== null){ clearTimeout(navAfterArriveTimer); navAfterArriveTimer = null; }
  if(navMotion) navMotion.speed = 0;     // no more readings: the arrow stops where it is
  if(navWatchId !== null) navStopLocate();
}

// live location through Leaflet: map.locate({watch: true}) fires "locationfound" / "locationerror".
// Each reading is turned into the browser's position shape, so the rest of the navigator is unchanged.
const NAV_LOCATE_OPTIONS = { watch: true, setView: false, enableHighAccuracy: true, maximumAge: 0, timeout: 30000 };
let navLocateHandlers = null;

// The campus boundary: a reading far outside it cannot be right while you are on campus
const NAV_OFF_CAMPUS_M = 300;     // further than this outside the campus (beyond the accuracy) = off campus
const NAV_GLITCH_RUN = 3;         // this many off-campus readings in a row means you really left
let navOutsideRun = 0;

// how far a point is outside the campus area, in metres (0 when inside)
function navOffCampusM(lat, lng){
  const b = navCampusBox;
  if(!b) return 0;
  const cLat = Math.max(b.south, Math.min(b.north, lat)), cLng = Math.max(b.west, Math.min(b.east, lng));
  return cLat === lat && cLng === lng ? 0 : navMap.distance([lat, lng], [cLat, cLng]);
}

function navStartLocate(onPosition, onError){
  navStopLocate();
  navOutsideRun = 0;
  const found = e => {
    const off = navOffCampusM(e.latlng.lat, e.latlng.lng);
    const outside = off > e.accuracy + NAV_OFF_CAMPUS_M;
    navOutsideRun = outside ? navOutsideRun + 1 : 0;
    // already found on campus: one odd far-away reading is a GPS glitch, skip it
    if(outside && navLastPos && navOutsideRun < NAV_GLITCH_RUN) return;
    reading(e, off, outside);
  };
  const reading = (e, off, outside) => onPosition({
    coords: {
      latitude: e.latlng.lat, longitude: e.latlng.lng, accuracy: e.accuracy,
      heading: typeof e.heading === 'number' ? e.heading : null,
      speed: typeof e.speed === 'number' ? e.speed : null,
    },
    timestamp: e.timestamp || Date.now(),
    offCampusM: off, offCampus: outside,
  });
  const failed = e => onError({ code: e.code, message: e.message });
  navMap.on('locationfound', found);
  navMap.on('locationerror', failed);
  navLocateHandlers = { found, failed };
  navWatchId = 'leaflet';
  navMap.locate(NAV_LOCATE_OPTIONS);
}

function navStopLocate(){
  if(navLocateHandlers){
    navMap.off('locationfound', navLocateHandlers.found);
    navMap.off('locationerror', navLocateHandlers.failed);
    navLocateHandlers = null;
  }
  if(navMap) navMap.stopLocate();
  navWatchId = null;
}

// "Full? Next parking" + "Parked here": shown on car + walk routes until the driver has parked
function navSyncParkButtons(){
  const r = navRoutes[navRouteIdx];
  const show = !!(navDest && r && r.parking && !navParkedAt);
  const pf = document.getElementById('parkFullBtn');
  const pk = document.getElementById('parkedBtn');
  if(pf) pf.hidden = !show;
  if(pk) pk.hidden = !show;
}

function setStopButton(visible){
  const stopBtn = document.getElementById('stopRouteBtn');
  stopBtn.hidden = !visible;
  if(visible) stopBtn.textContent = '✕ End route';
  const closed = document.getElementById('closedBtn');
  if(closed) closed.hidden = !visible;   // shown while a route is active
  if(!visible){
    ['parkFullBtn', 'parkedBtn'].forEach(id => { const b = document.getElementById(id); if(b) b.hidden = true; });
    navRoutes = []; navRouteIdx = 0; navReply = null;
    navRenderRouteChoices();
    navRenderLots();                     // no route: no parking highlighted
  }
}

// Drawing a route.
const NAV_LINE = {
  drive: { color: '#0b3d91', weight: 8, opacity: 0.95 },
  walk:  { color: '#16a34a', weight: 6, opacity: 0.95 },
};
const NAV_LEG_EMOJI = { drive: '🚗', walk: '👣' };
const NAV_WRONG = '#d93025';

function navParkIcon(){
  return L.divIcon({ className: 'nav-park-icon', html: '<span>P</span>', iconSize: [26, 26], iconAnchor: [13, 13] });
}

function navDrawRoute(fit){
  const data = navRoutes[navRouteIdx];
  if(!data) return;
  if(navRouteLayer) navRouteLayer.remove();
  if(navParkMarker){ navParkMarker.remove(); navParkMarker = null; }
  const layers = [];
  navRouteLines = [];
  const legs = data.legs || [{ mode: 'walk', coordinates: data.coordinates }];

  // white outline under the route so it stands out on the satellite photo
  legs.forEach(leg => layers.push(L.polyline(leg.coordinates, {
    color: '#ffffff', weight: leg.mode === 'drive' ? 13 : 11, opacity: 0.85, interactive: false })));
  // the short pieces from you to the road/path and from the path to the door: thinner, same colours
  // (car only: you start in the car, so the first piece is driven; the last piece is always on foot)
  const thin = mode => ({ ...NAV_LINE[mode], weight: 4, opacity: 0.85, interactive: false });
  const startMode = legs[0] && legs[0].mode === 'drive' ? 'drive' : 'walk';
  const sc = L.polyline(data.start_connector, thin(startMode)), ec = L.polyline(data.end_connector, thin('walk'));
  layers.push(sc, ec);
  navRouteLines.push({ line: sc, style: thin(startMode) }, { line: ec, style: thin('walk') });
  legs.forEach(leg => {
    const style = { ...(NAV_LINE[leg.mode] || NAV_LINE.walk), interactive: false };
    const line = L.polyline(leg.coordinates, style);
    layers.push(line);
    navRouteLines.push({ line, style });
  });
  navDoneLine = L.polyline([], { color: '#8a94a6', weight: 8, opacity: 0.9, interactive: false });
  layers.push(navDoneLine);
  // 🚗 / 👣 badge half-way along each part (only when there is more than one kind, or car only)
  if(legs.length > 1 || data.mode === 'drive'){
    legs.forEach(leg => {
      const mid = navMidpoint(leg.coordinates);
      if(mid) layers.push(L.marker(mid, { icon: navLegBadge(leg.mode), interactive: false, keyboard: false, zIndexOffset: 800 }));
    });
  }
  if(data.drop_off){
    layers.push(L.marker([data.drop_off.lat, data.drop_off.lng], {
      icon: L.divIcon({ className: 'nav-drop-icon', html: '<span>🚙 Drop-off</span>', iconSize: [84, 24], iconAnchor: [42, -8] }),   // label just under the point (the pin is above)
      interactive: false, keyboard: false, zIndexOffset: 850 }));
  }
  navRouteLayer = L.layerGroup(layers).addTo(navMap);

  // the parking of this route: its P marker grows (all P markers are always on the map)
  navRenderLots();
  navSyncParkButtons();
  navShowLegend(data);

  navEndSnap = data.end_connector[0];
  navBuildTrack(data);

  if(fit){
    navMap.fitBounds(
      L.latLngBounds([...data.coordinates, ...data.start_connector, ...data.end_connector]),
      { padding: [40, 40], maxZoom: 18 }
    );
  }
  navRenderRouteChoices();
  navMessage(navRouteSummary(data));
}

function navFmtMin(m){ return `${Math.max(1, Math.round(m))} min`; }

// point half-way along a line (by distance), for the 🚗 / 👣 badges
function navMidpoint(coords){
  if(!coords || coords.length < 2) return null;
  let total = 0;
  const d = [];
  for(let i = 1; i < coords.length; i++){ d.push(navMap.distance(coords[i - 1], coords[i])); total += d[i - 1]; }
  if(total < 40) return null;                     // too short for a badge
  let half = total / 2;
  for(let i = 0; i < d.length; i++){
    if(half <= d[i]){
      const t = d[i] ? half / d[i] : 0, a = coords[i], b = coords[i + 1];
      return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
    }
    half -= d[i];
  }
  return coords[coords.length - 1];
}

function navLegBadge(mode){
  return L.divIcon({ className: `nav-leg-badge ${mode}`, html: `<span>${NAV_LEG_EMOJI[mode] || '👣'}</span>`,
                     iconSize: [26, 26], iconAnchor: [13, 13] });
}

// legend in the corner: the colours used by the route on screen (+ what the P markers mean)
function navShowLegend(data){
  const box = navLegend && navLegend.getContainer();
  if(!box) return;
  if(!data){ box.hidden = true; return; }
  const legs = data.legs || [{ mode: 'walk' }];
  const modes = [...new Set(legs.map(l => l.mode))];
  if(data.end_connector && !modes.includes('walk') && (data.walk_m || 0) >= 5) modes.push('walk');
  box.replaceChildren();
  const label = { drive: 'Drive (car)', walk: 'Walk (on foot)' };
  modes.forEach(m => {
    const row = navEl('div', 'lg-row');
    row.append(navEl('span', `leg-key leg-key-${m}`), navEl('span', '', `${NAV_LEG_EMOJI[m]} ${label[m]}`));
    box.appendChild(row);
  });
  if(data.mode !== 'walk' && navLots.length){
    const row = navEl('div', 'lg-row');
    const g = navEl('span', 'lg-p', 'P'); g.style.background = '#188038';
    const r = navEl('span', 'lg-p', 'P'); r.style.background = '#d93025';
    row.append(g, navEl('span', '', 'parking'), r, navEl('span', '', 'reported full'));
    box.appendChild(row);
  }
  box.hidden = false;
}

function navRouteSummary(data){
  let msg, pre = '', post = '';       // pre/post: parking notes around "From X to <destination>: ..."
  if(data.mode === 'car' && data.parking){
    msg = `${navDest.name}: 🚗 drive ${Math.round(data.drive_m)} m to ${data.parking.name}, park, then 👣 walk ${Math.round(data.walk_m)} m · about ${navFmtMin(data.time_min)} in total`;
    const tried = navSkipParking.map(id => (navLots.find(l => l.id === id) || {}).name).filter(Boolean);
    if(data.all_parkings_full){
      if(tried.length){
        pre = `⚠ Every parking was full. Checking ${data.parking.name} again — cars leave all the time, so a space may have opened up.`;
        post = 'Still full? Tap "Full? Next parking".';
      } else {
        pre = '⚠ Every parking is reported full right now, so this goes to the closest one (a space may open up as cars leave).';
      }
    } else if(tried.length){
      const n = tried.length + 1, total = navLots.length || n;
      pre = `🅿️ You found ${tried.join(', ')} full, so now trying ${data.parking.name} (parking ${n} of ${total}).`;
      post = 'Full here too? Tap "Full? Next parking" again.';
    } else if(data.full_skipped && data.full_skipped.length){
      const names = data.full_skipped.map(p => p.name).join(', ');
      pre = `🅿️ ${names} ${data.full_skipped.length > 1 ? 'are' : 'is'} reported full by other drivers, so you are sent to ${data.parking.name}.`;
    }
    if(navReply && navReply.walk_only_min && navReply.walk_only_min <= data.time_min){
      msg += ` (walking all the way takes about ${navFmtMin(navReply.walk_only_min)}, so walking is just as quick)`;
    }
  } else if(data.mode === 'drive'){
    msg = `${navDest.name}: 🚙 drive ${Math.round(data.drive_m)} m to the drop-off point`;
    if((data.walk_m || 0) >= 5) msg += `, then 👣 ${Math.round(data.walk_m)} m to the door`;
    msg += ` · about ${navFmtMin(data.time_min)}`;
    const np = data.nearby_parking;
    if(np){
      const full = np.skipped_full && np.skipped_full.length ? ` (${np.skipped_full.join(', ')} ${np.skipped_full.length > 1 ? 'are' : 'is'} full)` : '';
      msg += np.full
        ? `. Need to park? Every parking is reported full; the closest is ${np.name}.`
        : `. Need to park? The nearest parking is ${np.name}${np.walk_m ? `, ${Math.round(np.walk_m)} m walk` : ''}${full}.`;
    }
  } else {
    msg = `${navDest.name}: 👣 ${Math.round(data.distance_meters)} m · about ${navFmtMin(data.time_min || data.estimated_walk_time_min)} walk`;
  }
  if(navStart) msg = `From ${navStart.name} to ${msg}`;
  else if(navReply && navReply.off_campus) msg += ` (you seem to be off campus, so this route starts at the ${navReply.start_name})`;
  else if(navReply && navReply.start_name !== 'your location') msg += ` (starting from the ${navReply.start_name})`;
  if(pre) msg = `${pre} ${msg}`;
  if(post) msg += `. ${post}`;
  if(navRoutes.length > 1) msg += navRouteIdx === 0
    ? ` This is the ${data.mode === 'car' ? 'recommended' : 'shortest'} route; tap "Alternate route" to see ${navRoutes.length - 1 === 1 ? 'another way' : 'other ways'}.`
    : ` This is alternate route ${navRouteIdx}.`;
  if(navNote) msg += ` ${navNote}`;
  return msg;
}

// route choices (the "Alternate route" button and the list in the side card)
function navRouteLabel(r, i){
  if(i === 0) return r.mode === 'car' ? 'Recommended' : r.mode === 'drive' ? 'Quickest' : 'Shortest';
  return `Alternate ${i}`;
}

function navRenderRouteChoices(){
  const altBtn = document.getElementById('altRouteBtn');
  const list = document.getElementById('routeChoices');
  const many = navRoutes.length > 1;
  if(altBtn){
    altBtn.hidden = !many;
    if(many){
      const next = (navRouteIdx + 1) % navRoutes.length;
      altBtn.textContent = next === 0 ? `↩ ${navRouteLabel(navRoutes[0], 0)} route` : '🔀 Alternate route';
      altBtn.title = `Route ${navRouteIdx + 1} of ${navRoutes.length} shown. Tap for the next one.`;
    }
  }
  if(!list) return;
  list.replaceChildren();
  list.hidden = navRoutes.length === 0;
  if(!navRoutes.length) return;
  const base = navRoutes[0];
  navRoutes.forEach((r, i) => {
    const btn = navEl('button', 'route-choice' + (i === navRouteIdx ? ' active' : ''));
    btn.type = 'button';
    const top = navEl('span', 'rc-top');
    top.append(navEl('strong', '', navRouteLabel(r, i)), navEl('span', 'rc-time', navFmtMin(r.time_min)));
    const extra = i === 0 ? '' : ` · +${Math.max(0, Math.round(r.distance_meters - base.distance_meters))} m`;
    const how = r.mode === 'car' && r.parking
      ? `🚗 ${Math.round(r.drive_m)} m + 👣 ${Math.round(r.walk_m)} m · ${r.parking.name}${r.parking.full ? ' (full)' : ''}`
      : r.mode === 'drive'
      ? `🚙 ${Math.round(r.drive_m)} m${extra}`
      : `👣 ${Math.round(r.distance_meters)} m${extra}`;
    btn.append(top, navEl('span', 'rc-how', how));
    btn.addEventListener('click', () => navChooseRoute(i));
    list.appendChild(btn);
  });
  if(navRoutes.length === 1) list.appendChild(navEl('p', 'rc-note', 'No other sensible route for this trip.'));
}

function navChooseRoute(i){
  if(!navRoutes.length || i === navRouteIdx) return;
  navRouteIdx = i;
  navDrawRoute(true);
  if(navLastPos) navFollowPosition(navLastPos.lat, navLastPos.lng, navLastPos.acc, true);
}
document.getElementById('altRouteBtn').addEventListener('click', () => {
  if(navRoutes.length > 1) navChooseRoute((navRouteIdx + 1) % navRoutes.length);
});

// Following the student along the FIXED route (no new route is asked for while they move).
function navBuildTrack(data){
  // one line: start connector -> legs -> end connector, with distance along it
  const pts = [], legOf = [];
  const push = (list, mode) => list.forEach(p => {
    const last = pts[pts.length - 1];
    if(last && last[0] === p[0] && last[1] === p[1]) return;
    pts.push(p); legOf.push(mode);
  });
  push(data.start_connector, data.mode === 'drive' ? 'drive' : 'walk');
  (data.legs || [{ mode: 'walk', coordinates: data.coordinates }]).forEach(l => push(l.coordinates, l.mode));
  push(data.end_connector, 'walk');

  const lat0 = pts[0][0], kx = 111320 * Math.cos(lat0 * Math.PI / 180), ky = 110540;
  const xy = p => [(p[1] - pts[0][1]) * kx, (p[0] - lat0) * ky];
  const cum = [0];
  for(let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + navMap.distance(pts[i - 1], pts[i]));
  let parkAt = null;
  if(data.mode === 'car' && data.legs && data.legs.length > 1){
    const p = data.legs[0].coordinates[data.legs[0].coordinates.length - 1];
    const i = pts.findIndex(q => q[0] === p[0] && q[1] === p[1]);
    if(i >= 0) parkAt = cum[i];
  }
  navTrack = { pts, xy: pts.map(xy), legOf, cum, total: cum[cum.length - 1], toXY: xy, parkAt };
  navFollow = { joined: false, max: 0, bad: 0, wrong: false, parkedShown: false };
}

// closest point of the route to (lat, lng); near ties go to the part closest to the progress so far
function navProject(lat, lng, near, slack){
  const T = navTrack, [px, py] = T.toXY([lat, lng]);
  const cands = [];
  for(let i = 0; i < T.xy.length - 1; i++){
    const [x1, y1] = T.xy[i], [x2, y2] = T.xy[i + 1];
    const dx = x2 - x1, dy = y2 - y1, L2 = dx * dx + dy * dy;
    const t = L2 ? Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / L2)) : 0;
    const qx = x1 + t * dx, qy = y1 + t * dy;
    cands.push({ i, t, dist: Math.hypot(px - qx, py - qy), along: T.cum[i] + t * (T.cum[i + 1] - T.cum[i]) });
  }
  const best = Math.min(...cands.map(c => c.dist));
  const ref = typeof near === 'number' ? near : navFollow ? navFollow.max : 0;
  return cands.filter(c => c.dist <= best + (slack || 10))
              .sort((a, b) => Math.abs(a.along - ref) - Math.abs(b.along - ref))[0];
}

function navPointAt(i, t){
  const a = navTrack.pts[i], b = navTrack.pts[i + 1];
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t];
}

function navFollowPosition(lat, lng, accuracy, quiet){
  if(!navTrack || !navFollow || !navDest) return;
  const T = navTrack, F = navFollow;
  const p = navProject(lat, lng);
  const driving = T.legOf[p.i] === 'drive';
  const limit = (driving ? NAV_OFF_ROUTE_DRIVE_M : NAV_OFF_ROUTE_WALK_M) + Math.min(accuracy || 0, 30) / 2;

  // arrived?
  const nearBuilding = navMap.distance([lat, lng], [navDest.lat, navDest.lng]) <= NAV_ARRIVE_M;
  const nearPathEnd = navEndSnap && navMap.distance([lat, lng], navEndSnap) <= NAV_ARRIVE_PATH_END_M;
  if(nearBuilding || (nearPathEnd && p.dist <= limit)){ navArrived(); return; }

  // not on the route yet (e.g. walking to the start of it from the gate or the car)
  if(!F.joined){
    if(p.dist <= limit){
      F.joined = true; F.max = p.along;
    } else {
      navShowWrongWay(false);
      if(!quiet) navMessage(`Head to the start of the route (the blue line) — it is ${Math.round(p.dist)} m away. ${navRouteSummary(navRoutes[navRouteIdx])}`);
      return;
    }
  }

  const off = p.dist > limit;
  const backwards = p.along < F.max - NAV_BACKWARDS_M;
  if(!off && !backwards) F.max = Math.max(F.max, p.along);
  F.bad = (off || backwards) ? F.bad + 1 : 0;

  // grey out what is done
  if(navDoneLine){
    const upto = Math.max(F.max, 0);
    let j = 0;
    while(j < T.cum.length - 2 && T.cum[j + 1] < upto) j++;
    const segLen = T.cum[j + 1] - T.cum[j];
    const tt = segLen ? Math.min(1, (upto - T.cum[j]) / segLen) : 0;
    navDoneLine.setLatLngs([...T.pts.slice(0, j + 1), navPointAt(j, tt)]);
  }

  // changed path / turned back for good: new route from here instead of nagging
  if(off && F.bad >= (navSimulate ? 2 : NAV_AUTO_REROUTE_FIXES)){ navReroute('left'); return; }
  if(backwards && F.max - p.along >= NAV_TURNED_BACK_M && F.bad >= 2){ navReroute('back'); return; }

  if(F.bad >= (navSimulate ? 1 : NAV_BAD_FIXES)){
    navShowWrongWay(true, off
      ? `You have left the route — you are ${Math.round(p.dist)} m away from it. Go back to the line, or keep going and a new route will be found from where you are.`
      : 'Wrong direction! You are moving away from your destination. Turn around, or keep going and another way will be found.');
    return;
  }
  if(F.bad === 0) navShowWrongWay(false);

  // car: tell the student when they reach the parking
  if(T.parkAt !== null && !F.parkedShown && F.max >= T.parkAt - 15){
    F.parkedShown = true;
    const r = navRoutes[navRouteIdx];
    navToast(`🅿️ Park at ${r.parking.name}, then walk ${Math.round(r.walk_m)} m to ${navDest.name}. Full? Tap "🅿️ Full? Next parking". Found a space? Tap "✓ Parked here".`);
  }
  if(quiet) return;
  const left = Math.max(0, T.total - F.max);
  let msg;
  if(T.parkAt !== null && F.max < T.parkAt - 15){
    const toPark = T.parkAt - F.max;
    msg = `🚗 ${Math.round(toPark)} m to ${navRoutes[navRouteIdx].parking.name}, then 👣 ${Math.round(T.total - T.parkAt)} m walk.`;
  } else if(navRoutes[navRouteIdx] && navRoutes[navRouteIdx].mode === 'drive'){
    msg = `🚙 ${Math.round(left)} m left to ${navDest.name} · about ${navFmtMin(left / NAV_DRIVE_M_PER_MIN)}.`;
  } else {
    msg = `👣 ${Math.round(left)} m left to ${navDest.name} · about ${navFmtMin(left / 80)}.`;
  }
  navMessage(msg);
}

// red route + banner on the map when the student goes the wrong way
function navShowWrongWay(on, text, silent){
  const banner = document.getElementById('wrongWayBanner');
  const was = navFollow && navFollow.wrong;
  if(navFollow) navFollow.wrong = !!on;
  navRouteLines.forEach(({ line, style }) => line.setStyle(on ? { ...style, color: NAV_WRONG } : style));
  if(banner){
    banner.hidden = !on;
    if(on) banner.querySelector('.ww-text').textContent = text;
  }
  if(on){
    navMessage('⚠ ' + text);
    if(!was && navigator.vibrate) try{ navigator.vibrate([200, 100, 200]); } catch(e){}
  } else if(was && !silent){
    navToast('✓ Back on the route.');
  }
}

// Short messages over the map. They queue up, so "near Library" is not wiped out by
// "passed Admin" a moment later.
const navToastQueue = [];
let navToastBusy = false;
function navToast(text){
  if(navToastQueue[navToastQueue.length - 1] === text) return;
  navToastQueue.push(text);
  if(navToastQueue.length > 4) navToastQueue.shift();
  if(!navToastBusy) navNextToast();
}
function navNextToast(){
  const el = document.getElementById('navToast');
  const text = navToastQueue.shift();
  if(!el || text === undefined){ navToastBusy = false; if(el) el.hidden = true; return; }
  navToastBusy = true;
  el.textContent = text;
  el.hidden = false;
  setTimeout(navNextToast, navToastQueue.length ? 2600 : 4000);
}

// buildings along the way
function navSideOf(lat, lng, bld){
  const diff = ((navBearing([lat, lng], [bld.lat, bld.lng]) - navHeading) % 360 + 540) % 360 - 180;
  if(Math.abs(diff) < 30) return 'ahead of you';
  if(Math.abs(diff) > 150) return 'behind you';
  return diff < 0 ? 'on your left' : 'on your right';
}

function navLogLandmark(text){
  const feed = document.getElementById('landmarkFeed');
  if(!feed) return;
  feed.hidden = false;
  const item = navEl('li', '', text);
  feed.prepend(item);
  while(feed.children.length > 5) feed.lastChild.remove();
}

function navResetLandmarks(){
  navLandmarks.clear();
  navLandmarksPrimed = false;
  const feed = document.getElementById('landmarkFeed');
  if(feed){ feed.replaceChildren(); feed.hidden = true; }
}

// Called for every position while a route is active.
function navCheckLandmarks(lat, lng){
  if(!navDest || !navMap) return;
  navBuildings.forEach(bld => {
    if(bld.id === navDest.id) return;                       // arrival has its own message
    const d = navMap.distance([lat, lng], [bld.lat, bld.lng]);
    const state = navLandmarks.get(bld.id);
    if(!navLandmarksPrimed){                                 // where the trip starts: say nothing
      if(d <= NAV_LANDMARK_NEAR_M) navLandmarks.set(bld.id, 'skip');
      return;
    }
    if(!state && d <= NAV_LANDMARK_NEAR_M){
      navLandmarks.set(bld.id, 'near');
      const text = `📍 You're just near ${bld.name} (${navSideOf(lat, lng, bld)}).`;
      navToast(text); navLogLandmark(text);
    } else if(state === 'near' && d >= NAV_LANDMARK_PASS_M){
      navLandmarks.set(bld.id, 'passed');
      const text = `✓ You've passed ${bld.name}.`;
      navToast(text); navLogLandmark(text);
    }
  });
  navLandmarksPrimed = true;
}

// changing path in the middle / closed road
// points every few metres along the current route, between two distances along it
function navSampleRoute(from, to, step = 8){
  const T = navTrack, out = [];
  if(!T) return out;
  for(let d = Math.max(0, from); d <= Math.min(T.total, to); d += step){
    let j = 0;
    while(j < T.cum.length - 2 && T.cum[j + 1] < d) j++;
    const seg = T.cum[j + 1] - T.cum[j];
    out.push(navPointAt(j, seg ? Math.min(1, (d - T.cum[j]) / seg) : 0));
  }
  return out;
}

function navDrawClosed(points){
  if(!points.length) return;
  if(!navClosedLayer) navClosedLayer = L.layerGroup().addTo(navMap);
  L.polyline(points, { color: '#5f6368', weight: 9, opacity: 0.55, interactive: false }).addTo(navClosedLayer);
  const mid = points[Math.floor(points.length / 2)];
  L.marker(mid, {
    icon: L.divIcon({ className: 'nav-closed-icon', html: '<span>🚧</span>', iconSize: [26, 26], iconAnchor: [13, 13] }),
    keyboard: false, interactive: false
  }).addTo(navClosedLayer);
}

function navClearClosed(){
  navAvoid = [];
  if(navClosedLayer){ navClosedLayer.remove(); navClosedLayer = null; }
}

// A NEW route from where the student is now (never from where the trip began). why = 'left' ->
// they took another path 'back' -> they turned around
function navReroute(why){
  if(!navDest || !navLastPos || !navTrack || !navFollow) return;
  if(navBusy) return;
  const now = Date.now();
  if(why !== 'closed' && why !== 'manual' && now - navLastReroute < NAV_REROUTE_COOLDOWN_MS) return;
  navLastReroute = now;

  const F = navFollow;
  if(why !== 'manual' || F.wrong){
    const from = why === 'closed' ? navProject(navLastPos.lat, navLastPos.lng).along : F.max;
    const pts = navSampleRoute(from + 6, from + NAV_AVOID_AHEAD_M);
    if(pts.length){
      navAvoid = navAvoid.concat(pts).slice(-60);
      navDrawClosed(pts);
    }
  }
  // already parked and walking? then the rest of the trip is on foot, not by car again
  const parked = !!navParkedAt || (navTrack.parkAt !== null && F.max >= navTrack.parkAt - 15);
  const mode = navMode === 'car' && parked ? 'walk' : navMode;

  const texts = {
    left: 'You took a different path — finding a new route from where you are...',
    back: 'You turned back — finding another way from where you are...',
    closed: '🚧 Road closed — finding another way from where you are...',
    manual: 'Finding a new route from where you are...',
  };
  navStart = null;                       // from now on the route starts where the student is
  navShowWrongWay(false, '', true);
  navMessage(texts[why] || texts.manual);
  navRequestRoute({ lat: navLastPos.lat, lng: navLastPos.lng }, true, { reroute: why, mode });
}

document.getElementById('rerouteBtn').addEventListener('click', () => navReroute('manual'));
document.getElementById('closedBtn').addEventListener('click', () => navReroute('closed'));

// One POST /api/route with a time limit. When the server is busy (503) or the network
// hiccups, wait a little and try again (1 s, 2 s, 4 s) before giving up.
const NAV_REQUEST_TIMEOUT_MS = 15000;
const NAV_RETRIES = 3;
async function navPostRoute(body, myId){
  let lastErr = null;
  for(let attempt = 0; attempt <= NAV_RETRIES; attempt++){
    if(attempt){
      const wait = 1000 * Math.pow(2, attempt - 1) + Math.random() * 300;   // jitter: users don't retry in step
      await new Promise(r => setTimeout(r, wait));
      if(myId !== navReqId) return null;          // the student moved on meanwhile
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), NAV_REQUEST_TIMEOUT_MS);
    try{
      const out = await api('/api/route', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: ctrl.signal
      });
      if(out.res.status === 503 || out.res.status === 502 || out.res.status === 504){
        lastErr = out;
        if(myId === navReqId) navMessage('Many students are asking for directions right now. Trying again...');
        continue;
      }
      return out;
    } catch(err){
      lastErr = err;                               // network error or timeout: try again
    } finally {
      clearTimeout(timer);
    }
  }
  if(lastErr && lastErr.res) return lastErr;
  throw lastErr || new Error('request failed');
}

// No exact location: do NOT guess a start (not even the main gate). Ask the student
// to place themselves on the map with "Set my location".
function navAskSetLocation(reason){
  navReqId++;
  navStopTracking();
  navClearRoute();
  navStart = null;
  navEndSnap = null;
  setStopButton(false);
  document.getElementById('stopRouteBtn').hidden = true;
  // navDest is kept, so the route is drawn as soon as the location is set
  const box = document.getElementById('needLocBanner');
  if(box){
    box.querySelector('.nl-text').textContent = reason;
    box.hidden = false;
  }
  document.getElementById('setLocBtn').classList.add('attention');
  navMessage(`📍 ${reason}`);
}
function navHideAskSetLocation(){
  const box = document.getElementById('needLocBanner');
  if(box) box.hidden = true;
  document.getElementById('setLocBtn').classList.remove('attention');
}
document.getElementById('needLocBtn').addEventListener('click', () => {
  navHideAskSetLocation();
  navSetPicking(true);
});

// Ask the server for routes ONCE: from a chosen building (navStart) or from the
// student's exact position (pos). The route then stays fixed while the student moves.
async function navRequestRoute(pos, force, opts = {}){
  if(!navDest || (navBusy && !force)) return;
  if(!navStart && !pos){
    navAskSetLocation('We could not get your exact location. Tap "Set my location", then tap where you are on the map.');
    return;
  }
  const myId = ++navReqId;
  navBusy = true;
  try{
    const body = { destination_id: navDest.id, mode: opts.mode || navMode };
    if(navAvoid.length) body.avoid = navAvoid;
    if(navSkipParking.length && body.mode !== 'walk') body.skip_parking = navSkipParking;
    if(navStart && navStart.custom) body.start = { lat: navStart.lat, lng: navStart.lng };
    else if(navStart) body.start_id = navStart.id;
    else body.start = { lat: pos.lat, lng: pos.lng };
    const out = await navPostRoute(body, myId);
    if(!out || myId !== navReqId || !navDest) return;   // destination changed or route ended meanwhile
    const {res, data} = out;
    if(data && (data.code === 'need_location' || data.code === 'off_campus')){
      navAskSetLocation(data.error);
      return;
    }
    if(!res.ok){ navMessage((data && data.error) || 'Could not calculate a route. Please try again.'); return; }
    navHideAskSetLocation();
    navReply = data;
    if(Array.isArray(data.full_parkings)) navMarkFull(data.full_parkings);   // the server's latest view
    navRoutes = Array.isArray(data.routes) && data.routes.length ? data.routes : [data];
    navRouteIdx = 0;                              // the shortest route first
    // re-route: keep the view, the student is mid-trip (new parking: show the whole new route)
    navDrawRoute(!opts.reroute || opts.reroute === 'parking');
    if(opts.reroute && opts.reroute !== 'parking' && navLastPos) navMap.panTo([navLastPos.lat, navLastPos.lng]);
    if(navLastPos){
      const r = navRoutes[0];
      if(!opts.reroute) navSetHeading(navBearing(r.coordinates[0], r.coordinates[Math.min(1, r.coordinates.length - 1)]));
      navFollowPosition(navLastPos.lat, navLastPos.lng, navLastPos.acc, true);
    }
    if(opts.reroute === 'parking'){
      const r = navRoutes[0];
      navToast(r.parking && !r.all_parkings_full
        ? `🅿️ New route to ${r.parking.name}: about ${navFmtMin(r.time_min)}.`
        : `⚠ Every parking was full. Checking ${r.parking ? r.parking.name : 'the closest parking'} again.`);
      navMessage(navRouteSummary(r));
    } else if(opts.reroute){
      const r = navRoutes[0];
      let t = `🔄 New route from where you are: ${Math.round(r.distance_meters)} m, about ${navFmtMin(r.time_min)}.`;
      if(navRoutes.length > 1) t += ' Tap "Alternate route" for other ways.';
      navToast(t);
      if(r.through_closed_m > 0) navToast('⚠ There is no way around the closed part, so this route still goes through it.');
      navMessage(`🔄 New route from where you are. ${navRouteSummary(r)}`);
    }
  } catch(err){
    console.error(err);
    if(err.message !== 'signed out') navMessage('Could not reach the navigation server. Check your connection and tap Get Route again.');
  } finally {
    navBusy = false;
  }
}

function navArrived(){
  const name = navDest ? navDest.name : 'your destination';
  navClearClosed();
  navReqId++;                 // discard any route request still in flight
  if(navFixTimer !== null){ clearTimeout(navFixTimer); navFixTimer = null; }
  navLastDest = navDest;
  navClearRoute();
  navDest = null;
  navEndSnap = null;
  setStopButton(false);
  // the route is done, but the arrow keeps showing where you are (it switches itself off later)
  if(navWatchId !== null){
    const stopBtn = document.getElementById('stopRouteBtn');
    stopBtn.textContent = '✕ Stop location';
    stopBtn.hidden = false;
    navAfterArriveTimer = setTimeout(navStopTracking, NAV_AFTER_ARRIVE_MS);
  }
  navMessage(`🎉 You have arrived at ${name}.`);
  navToast(`You have arrived at ${name}.`);
}

// "End route" button: stop following the student's location and clear the route
function navEndRoute(){
  const afterArrival = !navDest && navWatchId !== null;
  navClearClosed();
  navResetLandmarks();
  navReqId++;
  navStopTracking();
  navClearRoute();
  navDest = null;
  navStart = null;
  navEndSnap = null;
  setStopButton(false);
  navMessage(afterArrival ? 'Live location switched off.' : 'Route ended.');
}
document.getElementById('stopRouteBtn').addEventListener('click', navEndRoute);

// Runs for the first GPS fix (first = true) and then for every Leaflet locationfound update.
// Combine the readings of the last few seconds that are about as good as the best one:
// each counts more the more accurate it is, so one wobbly reading pulls less.
const NAV_AVERAGE_S = 8;
function navAverageFix(fixes, best){
  const now = performance.now(), bAcc = best.coords.accuracy;
  const use = fixes.filter(f => now - f.t <= NAV_AVERAGE_S * 1000 && f.pos.coords.accuracy <= bAcc * 2
    && navMap.distance([f.pos.coords.latitude, f.pos.coords.longitude], [best.coords.latitude, best.coords.longitude]) <= bAcc + f.pos.coords.accuracy);
  if(use.length < 2) return best;
  let w = 0, la = 0, ln = 0;
  for(const f of use){
    const k = 1 / (f.pos.coords.accuracy * f.pos.coords.accuracy);
    w += k; la += f.pos.coords.latitude * k; ln += f.pos.coords.longitude * k;
  }
  return { ...best, coords: { ...best.coords, latitude: la / w, longitude: ln / w } };
}

function navHandlePosition(pos, first){
  const { latitude: lat, longitude: lng, accuracy } = pos.coords;
  if(!first && accuracy > NAV_MAX_ACCURACY_M && !navSimulate){
    // too rough for route decisions, but near the destination (and after arriving) still move the arrow
    const target = navDest || navLastDest;
    const near = target && navMap.distance([lat, lng], [target.lat, target.lng]) <= NAV_ROUGH_SHOW_M;
    if(accuracy <= NAV_ROUGH_SHOW_M && (near || !navDest)) navDrawUser(lat, lng, accuracy);
    return;
  }

  // direction of travel: the compass/GPS heading when moving, otherwise from the last position
  const gpsHeading = pos.coords.heading;
  if(gpsHeading !== null && gpsHeading !== undefined && !isNaN(gpsHeading) && (pos.coords.speed || 0) > 0.5){
    navSetHeading(gpsHeading);
  } else if(navLastPos && navMap.distance([navLastPos.lat, navLastPos.lng], [lat, lng]) >= 3){
    navSetHeading(navBearing([navLastPos.lat, navLastPos.lng], [lat, lng]));
  }
  navDrawUser(lat, lng, accuracy, pos.coords);
  navLastPos = { lat, lng, acc: accuracy };
  if(!navDest) return;
  if(navTrack) navCheckLandmarks(lat, lng);

  if(first || (!navTrack && !navBusy)){
    navRequestRoute(navStart ? null : { lat, lng }, true);
    return;
  }
  navFollowPosition(lat, lng, accuracy, false);
}

function navGeoReason(err){
  if(!window.isSecureContext) return 'Live location only works on https:// pages (or localhost).';
  if(err.code === 1) return 'Location access was blocked (you can allow it in your browser settings).';
  if(err.code === 2) return 'Your location is not available right now.';
  return 'Getting your location took too long.';
}

// "Set my location": when the phone/laptop gives a wrong position, the student taps
// where they really are. That point becomes the starting point (no live tracking).
function navSetPicking(on){
  navPicking = on;
  if(on) navHideAskSetLocation();
  const btn = document.getElementById('setLocBtn');
  btn.classList.toggle('active', on);
  btn.textContent = on ? '👆 Tap the map...' : '📌 Set my location';
  document.body.classList.toggle('picking-location', on);
  if(on) navMessage('Tap your real position on the map.');
}

function navPickLocation(latlng){
  navSetPicking(false);
  // stop any live following / route that was in progress
  navReqId++;
  navStopTracking();
  navClearRoute();
  navDest = null; navStart = null; navEndSnap = null;
  setStopButton(false);

  navPicked = { id: NAV_PICKED_ID, name: 'the point you chose', lat: latlng.lat, lng: latlng.lng, custom: true };
  navDrawUser(navPicked.lat, navPicked.lng, 3);
  navLastPos = { lat: navPicked.lat, lng: navPicked.lng, acc: 3 };

  const from = document.getElementById('fromSelect');
  if(!from.querySelector(`option[value="${NAV_PICKED_ID}"]`)){
    const opt = document.createElement('option');
    opt.value = NAV_PICKED_ID;
    opt.textContent = 'Point I set on the map';
    from.insertBefore(opt, from.options[1] || null);   // right after "Current location"
  }
  from.value = NAV_PICKED_ID;

  if(document.getElementById('toSelect').value) getRoute();
  else navMessage('Location set. Now choose a destination and tap Get Route.');
}

document.getElementById('setLocBtn').addEventListener('click', () => navSetPicking(!navPicking));

// Parking areas: P markers (red = a driver reported it full) and reports.
function navLotIcon(lot, onRoute){
  const tried = navSkipParking.includes(lot.id);
  const cls = `nav-lot ${lot.full || tried ? 'full' : 'free'}${onRoute ? ' on-route' : ''}${tried ? ' tried' : ''}`;
  const size = onRoute ? 30 : 24;
  return L.divIcon({ className: `nav-lot-icon${onRoute ? ' lot-on-route' : ''}`, html: `<span class="${cls}">P</span>`,
                     iconSize: [size, size], iconAnchor: [size / 2, size / 2], popupAnchor: [0, -size / 2] });
}

function navLotCard(lot){
  const card = navEl('div', 'lot-card');
  card.appendChild(navEl('h4', '', lot.name));
  let state = lot.full ? '🔴 Reported full by a driver' : 'No full report — check when you get there';
  if(lot.full && lot.reported_min_ago !== null && lot.reported_min_ago !== undefined){
    state += ` · ${Math.max(1, Math.round(lot.reported_min_ago))} min ago`;
  }
  if(lot.capacity) state += ` · ${lot.capacity} bays`;
  card.appendChild(navEl('p', `lot-state ${lot.full ? 'full' : 'free'}`, state));
  const actions = navEl('div', 'lot-actions');
  const btn = navEl('button', lot.full ? 'lot-free' : 'lot-full', lot.full ? '✓ It has space now' : '✕ Report full');
  btn.type = 'button';
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    const wasFull = lot.full;                     // read now: the report replaces the lot list
    if(!wasFull && !navSkipParking.includes(lot.id)) navSkipParking.push(lot.id);
    const ok = await navReportParking(lot.id, wasFull ? 'available' : 'full');
    navMap.closePopup();
    if(!ok) return;
    // a car route is showing: if it uses a parking that just became full, or a better one
    // just freed up, get a fresh route so the student is sent to a parking with space
    const r = navRoutes[navRouteIdx];
    if(wasFull) navSkipParking = navSkipParking.filter(id => id !== lot.id);
    if(navDest && r && r.mode !== 'walk') navNewParkingRoute(null);
  });
  actions.appendChild(btn);
  card.appendChild(actions);
  return card;
}

function navSetLots(list){
  navLots = Array.isArray(list) ? list : [];
  navRenderLots();
}

function navRenderLots(){
  if(!navMap) return;
  if(!navLotLayer) navLotLayer = L.layerGroup().addTo(navMap);
  const r = navRoutes[navRouteIdx];
  const routeLot = r && r.parking ? r.parking.id : null;
  const keep = new Set();
  navLots.forEach(lot => {
    keep.add(lot.id);
    const onRoute = lot.id === routeLot;
    let m = navLotMarkers.get(lot.id);
    if(!m){
      m = L.marker([lot.lat, lot.lng], { keyboard: false, riseOnHover: true })
        .bindPopup(() => navLotCard(m._lot), { className: 'lot-popup', maxWidth: 240 })
        .addTo(navLotLayer);
      navLotMarkers.set(lot.id, m);
    }
    m._lot = lot;
    m.setIcon(navLotIcon(lot, onRoute));
    m.setZIndexOffset(onRoute ? 950 : 300);
    m.unbindTooltip();                  // no hover label: the driver sees on the spot if it is full
  });
  navLotMarkers.forEach((m, id) => { if(!keep.has(id)){ m.remove(); navLotMarkers.delete(id); } });
}

// the server's complete list of full parkings (sent with every car route)
function navMarkFull(ids){
  const full = new Set(ids);
  navLots.forEach(l => { l.full = full.has(l.id); });
  navRenderLots();
  if(navRoutes[navRouteIdx]) navShowLegend(navRoutes[navRouteIdx]);
}

async function navRefreshLots(){
  try{
    const {res, data} = await api('/api/parking');
    if(res.ok && data && Array.isArray(data.parkings)){
      if(data.full_minutes) navParkingMinutes = data.full_minutes;
      navSetLots(data.parkings);
    }
  } catch(e){ /* offline for a moment: keep what we have */ }
}

async function navReportParking(id, status, okText){
  try{
    const {res, data} = await api(`/api/parking/${encodeURIComponent(id)}/status`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status })
    });
    if(res.ok){
      if(Array.isArray(data.parkings)) navSetLots(data.parkings);
      navToast(okText || (status === 'full'
        ? `🅿️ Thanks! ${data.name} is marked full for other drivers (for about ${Math.round(navParkingMinutes)} min).`
        : `✓ Thanks! ${data.name} is no longer marked full.`));
      return true;
    }
    if(res.status === 429) return true;          // already reported a moment ago: that report counts
    navToast((data && data.error) || 'The report could not be sent.');
  } catch(e){
    navToast('The report could not be sent (no connection).');
  }
  return false;
}

// a new car route after a parking status change: from where the driver is now if they are being
// followed live
function navNewParkingRoute(freedId, fromLot){
  if(!navDest) return;
  if(freedId) navSkipParking = navSkipParking.filter(id => id !== freedId);
  const mode = navRoutes[navRouteIdx] && navRoutes[navRouteIdx].mode === 'drive' ? 'drive' : 'car';
  navShowWrongWay(false, '', true);
  if(fromLot){
    const live = navLastPos && (!navStart || (navSimulate && navFollow && navFollow.joined));
    if(live){                                     // GPS (or desk-test clicks): exactly where they are
      navStart = null;
      navRequestRoute({ lat: navLastPos.lat, lng: navLastPos.lng }, true, { reroute: 'parking', mode });
      return;
    }
    // no live position (the trip started from a building / a point set by hand):
    // the driver is standing at the full parking, so that is the new starting point
    if(navStartMarker){ navStartMarker.remove(); navStartMarker = null; }
    navStart = { id: '__parking__', name: fromLot.name, lat: fromLot.lat, lng: fromLot.lng, custom: true };
    navLastPos = { lat: fromLot.lat, lng: fromLot.lng, acc: 5 };
    navDrawUser(fromLot.lat, fromLot.lng, 5);    // the arrow moves to the parking
    navRequestRoute(null, true, { reroute: 'parking', mode });
    return;
  }
  // on the way (live GPS, a point set by hand, or already moving along the route): from here
  const fromHere = navLastPos && (!navStart || navStart.custom || (navFollow && navFollow.joined));
  if(fromHere){
    navStart = null;
    navRequestRoute({ lat: navLastPos.lat, lng: navLastPos.lng }, true, { reroute: 'parking', mode });
  } else {
    navRequestRoute(null, true, { reroute: 'parking', mode });
  }
}

// "🅿️ Parking full" button: the parking on the route is full -> report it
async function navParkingFull(){
  const r = navRoutes[navRouteIdx];
  if(!r || !r.parking || !navDest) return;
  const P = r.parking;
  const btn = document.getElementById('parkFullBtn');
  if(btn) btn.disabled = true;
  if(!navSkipParking.includes(P.id)) navSkipParking.push(P.id);   // skipped even if the report fails
  await navReportParking(P.id, 'full', `🅿️ ${P.name} flagged as full. Finding another parking...`);
  if(btn) btn.disabled = false;

  // every parking tried? say so, and start the search again from the closest one
  // (cars leave all the time, so a parking that was full a few minutes ago may have space now)
  const left = navLots.filter(l => !navSkipParking.includes(l.id));
  if(navLots.length && !left.length){
    const n = navSkipParking.length;
    navSkipParking = [];
    navToast(`⚠ You have tried all ${n} parkings. Starting again with the closest one — a space may have opened up.`);
  } else {
    navMessage(`🅿️ ${P.name} is full — finding the next parking (${left.length} left to try)...`);
  }
  navRenderLots();
  navNewParkingRoute(null, P);                    // the new route starts at this full parking
}
document.getElementById('parkFullBtn').addEventListener('click', navParkingFull);

// "✓ Parked here": the driver found a space -> stop the search, tell other drivers this
// parking has space, and carry on on foot (any new route from now on is a walking route)
async function navParkedHere(){
  const r = navRoutes[navRouteIdx];
  if(!r || !r.parking || !navDest) return;
  navParkedAt = r.parking.id;
  navSyncParkButtons();
  navSkipParking = navSkipParking.filter(id => id !== r.parking.id);
  if(navFollow) navFollow.parkedShown = true;
  navMessage(`✓ Parked at ${r.parking.name}. 👣 Walk ${Math.round(r.walk_m)} m to ${navDest.name} — follow the green line.`);
  await navReportParking(r.parking.id, 'available', `✓ Parked at ${r.parking.name}. Now walk ${Math.round(r.walk_m)} m to ${navDest.name}.`);
  navRenderLots();
}
document.getElementById('parkedBtn').addEventListener('click', navParkedHere);

// Walk / Car + walk / Car only switch
function navSyncModeButtons(){
  document.querySelectorAll('[data-nav-mode]').forEach(b => {
    const on = b.dataset.navMode === navMode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
}
document.querySelectorAll('[data-nav-mode]').forEach(b => b.addEventListener('click', () => {
  if(b.dataset.navMode === navMode) return;
  navMode = b.dataset.navMode;
  try{ localStorage.setItem('navMode', navMode); } catch(e){}
  navSyncModeButtons();
  // a route is showing: get the same trip in the new mode, from where the student is now
  if(navDest){
    navShowWrongWay(false, '', true);
    navMessage({ car: 'Finding a route by car + walking...', drive: 'Finding a route by car...' }[navMode] || 'Finding a walking route...');
    navRequestRoute(!navStart && navLastPos ? { lat: navLastPos.lat, lng: navLastPos.lng } : null, true);
  }
}));

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
  if(fromValue === NAV_PICKED_ID && !navPicked){
    navMessage('Tap "Set my location", then tap your real position on the map.');
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
  navRoutes = []; navRouteIdx = 0; navReply = null;
  navClearClosed();
  navResetLandmarks();
  navSkipParking = [];
  navParkedAt = null;
  navDest = dest;
  navStart = fromValue === 'current' ? null
           : fromValue === NAV_PICKED_ID ? navPicked
           : (navBuildings.get(fromValue) || null);
  navEndSnap = null;
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

  // From a chosen building (or a point set by hand): one fixed route
  if(navStart){
    if(!navStart.custom){
      navStartMarker = L.circleMarker([navStart.lat, navStart.lng], {
        radius: 9, color: '#fff', weight: 3, fillColor: NAV_COLORS.route, fillOpacity: 1, bubblingMouseEvents: false
      }).addTo(navMap);
      if(navSimulate){ navLastPos = { lat: navStart.lat, lng: navStart.lng, acc: 5 }; navDrawUser(navStart.lat, navStart.lng, 5); }
    }
    navRequestRoute(null, true);
    return;
  }

  // From the current location: the route is requested once, then the arrow follows the student
  if(navSimulate){
    if(navLastPos){
      navMessage('Simulation: click the map to move the arrow along (or away from) the route.');
      navRequestRoute({ lat: navLastPos.lat, lng: navLastPos.lng }, true);
    } else {
      navMessage('Simulation: click the map where you are standing; the route starts there.');
    }
    return;
  }
  if(!('geolocation' in navigator)){
    navAskSetLocation('This browser cannot share your location. Tap "Set my location", then tap where you are on the map.');
    return;
  }

  // Wait for a good fix instead of trusting the first reading: the browser's first answer is
  // often a rough Wi-Fi/IP guess. The waiting time only starts with the first reading, so the
  // "Allow location?" prompt does not use it up.
  navStopTracking();
  navMessage('Finding your current location...');
  let settled = false, best = null, geoProblem = '', farAway = null;
  const fixes = [];
  const settle = pos => {
    if(settled) return;
    settled = true;
    if(navFixTimer !== null){ clearTimeout(navFixTimer); navFixTimer = null; }
    if(!pos && farAway){
      const away = farAway.offCampusM;
      const where = away >= 1000 ? `about ${Math.round(away / 1000)} km` : `about ${Math.round(away / 10) * 10} m`;
      navStopTracking();
      navAskSetLocation(`Your phone puts you ${where} outside the UNIZULU campus. Live directions only work on campus. If you are on campus, tap "Set my location", then tap where you are on the map.`);
      return;
    }
    if(!pos){
      navAskSetLocation(`${geoProblem || 'We could not get your location.'} Tap "Set my location", then tap where you are on the map.`);
      return;
    }
    pos = navAverageFix(fixes, pos);
    const acc = Math.round(pos.coords.accuracy);
    if(acc > NAV_MAX_ACCURACY_M){
      // a very rough guess (Wi-Fi / IP / "approximate location") cannot start a route
      navAskSetLocation(`We could only find your location to within about ${acc} m, which is not exact enough. Tap "Set my location", then tap where you are on the map (or go outside and turn on precise location).`);
      return;
    }
    if(acc > NAV_GOOD_ACCURACY_M){
      // usable but rough: start anyway, the arrow and route improve as better readings come in
      navNote = `(Your location is approximate, about ${acc} m. It gets better as you move, or tap "Set my location".)`;
    }
    navHandlePosition(pos, true);      // first fix: position + the route
  };
  // safety net: no reading at all for a long time
  navFixTimer = setTimeout(() => settle(best), NAV_NO_FIX_MS);
  let gotReading = false, unavailableWait = false, offWait = false;
  navStartLocate(
    pos => {
      const acc = pos.coords.accuracy;
      if(settled){
        if(acc <= NAV_GOOD_ACCURACY_M && navNote.startsWith('(Your location')) navNote = '';
        navHandlePosition(pos, false);
        return;
      }
      if(!gotReading){
        // first reading: now give the GPS a fair chance to get better
        gotReading = true;
        clearTimeout(navFixTimer);
        navFixTimer = setTimeout(() => settle(best), NAV_FIX_WAIT_MS);
      }
      if(pos.offCampus){
        // off campus: keep the closest one in case nothing better comes
        if(!farAway || pos.offCampusM < farAway.offCampusM) farAway = pos;
        // only off-campus readings so far: give the GPS 10 seconds to find the campus
        if(!gotReading && !offWait){ offWait = true; clearTimeout(navFixTimer); navFixTimer = setTimeout(() => settle(best), 10000); }
        navMessage('Finding your current location... (your phone puts you outside the campus, waiting for a better reading)');
        return;
      }
      fixes.push({ pos, t: performance.now() });
      navDrawUser(pos.coords.latitude, pos.coords.longitude, acc);   // arrow + accuracy circle follow the improving fix
      if(!best || acc < best.coords.accuracy) best = pos;
      if(acc <= NAV_GOOD_ACCURACY_M) settle(pos);
      else navMessage(`Improving accuracy... currently about ${Math.round(acc)} m`);
    },
    err => {
      if(settled){ if(err.code === 1) navMessage(navGeoReason(err)); return; }
      geoProblem = navGeoReason(err);
      if(err.code === 2){
        // "unavailable" is often temporary: give it 10 more seconds (unless readings are coming in)
        if(!gotReading && !unavailableWait){
          unavailableWait = true; clearTimeout(navFixTimer); navFixTimer = setTimeout(() => settle(best), 10000);
        }
        return;
      }
      if(err.code !== 1 && best) return;   // took too long but we have a reading: the timer will use it
      navStopTracking();
      settle(err.code === 1 ? null : best);
    }
  );
}
