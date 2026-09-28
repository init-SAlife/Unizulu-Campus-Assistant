// ---------- Tab switching ----------
document.querySelectorAll('.tab-btn').forEach(btn=>{
  btn.addEventListener('click', ()=>{
    document.querySelectorAll('.tab-btn').forEach(b=>b.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v=>v.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById(btn.dataset.view).classList.add('active');
  });
});

// ---------- Chat (wired to Flask /chat -> Chatbot.py) ----------
function appendMessage(text, who){
  const wrap = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row ' + who;
  row.innerHTML = `<div class="avatar">${who==='user' ? '🧑' : '📖'}</div><div class="bubble"><p>${text}</p></div>`;
  wrap.appendChild(row);
  row.scrollIntoView({behavior:'smooth', block:'end'});
  return row;
}

function appendUploadResult(message, data){
  const details = data && data.failed_files && data.failed_files.length
    ? ' Some files could not be processed.'
    : '';
  appendMessage(message + details, 'bot');
}

// ---------- Staged attachments: pick files, preview them, remove before
// sending -- nothing is uploaded until the send button is actually pressed ----------
let pendingFiles = [];

function renderAttachmentPreview(){
  const wrap = document.getElementById('attachmentPreview');
  wrap.innerHTML = '';
  pendingFiles.forEach((file, i) => {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';
    chip.innerHTML = `<span class="attachment-name">📎 ${file.name}</span><button type="button" class="attachment-remove" aria-label="Remove ${file.name}">×</button>`;
    chip.querySelector('.attachment-remove').addEventListener('click', () => {
      pendingFiles.splice(i, 1);
      renderAttachmentPreview();
    });
    wrap.appendChild(chip);
  });
  wrap.style.display = pendingFiles.length ? 'flex' : 'none';
}

document.getElementById('uploadBtn').addEventListener('click', ()=>{
  document.getElementById('fileInput').click();
});
document.getElementById('fileInput').addEventListener('change', event=>{
  pendingFiles = pendingFiles.concat(Array.from(event.target.files));
  renderAttachmentPreview();
  event.target.value = '';
});

async function uploadPendingFiles(files){
  const documentType = document.getElementById('documentType').value;
  const formData = new FormData();
  formData.append('document_type', documentType);
  files.forEach(file => formData.append('files', file));

  const thinkingRow = appendMessage('Processing document...', 'bot');
  try{
    const res = await fetch('/upload', {method:'POST', body:formData});
    const data = await res.json();
    thinkingRow.remove();
    if(!res.ok){
      appendMessage(data.error || 'The document could not be processed.', 'bot');
      return false;
    }
    appendUploadResult(data.message, data.data);
    return true;
  } catch(err){
    thinkingRow.remove();
    appendMessage('Could not reach the document processing backend.', 'bot');
    console.error(err);
    return false;
  }
}

async function sendChat(){
  const input = document.getElementById('chatInput');
  const sendBtn = document.querySelector('.send-btn');
  const uploadBtn = document.getElementById('uploadBtn');
  const val = input.value.trim();

  if(!val && pendingFiles.length === 0) return;

  input.disabled = true;
  sendBtn.disabled = true;
  uploadBtn.disabled = true;

  // upload any staged attachments first -- pass them explicitly so
  // nothing is lost between clearing the preview and the actual request
  if(pendingFiles.length > 0){
    const filesToSend = pendingFiles;
    pendingFiles = [];
    renderAttachmentPreview();
    await uploadPendingFiles(filesToSend);
  }

  if(!val){
    input.disabled = false;
    sendBtn.disabled = false;
    uploadBtn.disabled = false;
    input.focus();
    return;
  }

  appendMessage(val, 'user');
  input.value = '';

  const thinkingRow = appendMessage('Thinking...', 'bot');

  try{
    const res = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: val })
    });

    const data = await res.json();
    thinkingRow.remove();

    if(!res.ok){
      appendMessage(data.error || 'Something went wrong. Please try again.', 'bot');
    } else {
      appendMessage(data.answer, 'bot');
    }
  } catch(err){
    thinkingRow.remove();
    appendMessage('Could not reach the assistant backend. Is the Flask server running?', 'bot');
    console.error(err);
  } finally {
    input.disabled = false;
    sendBtn.disabled = false;
    uploadBtn.disabled = false;
    input.focus();
  }
}

document.getElementById('chatInput').addEventListener('keydown', e=>{
  if(e.key === 'Enter') sendChat();
});

// Speech recognition runs in the browser; the transcript is sent through the existing /chat endpoint.
const micBtn = document.getElementById('micBtn');
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition;

if(SpeechRecognition){
  recognition = new SpeechRecognition();
  recognition.lang = 'en-ZA';
  recognition.interimResults = false;
  recognition.continuous = false;

  micBtn.addEventListener('click', ()=>{
    if(micBtn.classList.contains('listening')){
      recognition.stop();
      return;
    }

    recognition.start();
    micBtn.classList.add('listening');
    micBtn.title = 'Stop listening';
  });

  recognition.addEventListener('result', event=>{
    const transcript = event.results[0][0].transcript.trim();
    document.getElementById('chatInput').value = transcript;
    sendChat();
  });

  recognition.addEventListener('error', event=>{
    console.error('Speech recognition error:', event.error);
    if(event.error !== 'aborted'){
      appendMessage('I could not hear that. Please check microphone permission and try again.', 'bot');
    }
  });

  recognition.addEventListener('end', ()=>{
    micBtn.classList.remove('listening');
    micBtn.title = 'Speak';
  });
} else {
  micBtn.disabled = true;
  micBtn.title = 'Speech recognition is not supported by this browser';
}

function askSuggested(){
  document.getElementById('chatInput').value = 'When does semester 2 registration open?';
  sendChat();
}

// ---------- Campus Navigator: Leaflet map + live GPS + Flask /api/route ----------
const NAV_ARRIVE_M = 20;           // this close to the building (or the end of the path) = arrived
const NAV_MIN_MOVE_M = 5;          // don't ask for a new route unless the user moved this far...
const NAV_MIN_INTERVAL_MS = 3000;  // ...and at least this long has passed
const NAV_MAX_ACCURACY_M = 100;    // ignore live GPS updates that are less accurate than this
const navSimulate = new URLSearchParams(location.search).has('simulate'); // index.html?simulate=1

let navMap = null, navInit = null;
const navBuildings = new Map();
let navUser = null, navAccuracy = null, navRouteLayer = null, navDestMarker = null;
let navDest = null, navEndSnap = null, navWatchId = null, navLast = null;
let navBusy = false, navReqId = 0, navNote = '';

function navMessage(text){
  const box = document.getElementById('routeResult');
  if(!box) return;
  box.style.display = 'block';
  box.textContent = text;
}

// Builds the map, restricted to the campus area, and fills the destination list.
function initNavigator(){
  if(navInit) return navInit;
  navInit = (async () => {
    const mapEl = document.getElementById('campusMap');
    if(!mapEl) throw new Error('#campusMap element not found in index.html');

    const res = await fetch('/api/campus');
    if(!res.ok) throw new Error('Could not load campus data');
    const campus = await res.json();

    const b = campus.bounds;
    const bounds = L.latLngBounds([b.south, b.west], [b.north, b.east]);

    navMap = L.map(mapEl, { maxZoom: 19, maxBoundsViscosity: 1.0 });
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19,
      attribution: '&copy; OpenStreetMap contributors'
    }).addTo(navMap);

    navMap.invalidateSize();
    navMap.fitBounds(bounds.pad(0.05));
    navMap.setMinZoom(navMap.getZoom());       // can't zoom out past the campus
    navMap.setMaxBounds(bounds.pad(0.25));     // can't pan away from the campus

    const select = document.getElementById('toSelect');
    select.innerHTML = '';
    select.add(new Option('Select destination…', ''));

    campus.buildings.forEach(bld => {
      navBuildings.set(bld.id, bld);
      const extra = bld.description && bld.description.toLowerCase() !== bld.name.toLowerCase()
        ? ` (${bld.description})` : '';
      select.add(new Option(bld.name + extra, bld.id));

      const marker = L.circleMarker([bld.lat, bld.lng], {
        radius: 6, color: '#0b1f3a', weight: 2, fillColor: '#d4a017', fillOpacity: 1,
        bubblingMouseEvents: false
      }).addTo(navMap);
      const tip = document.createElement('span');
      tip.textContent = bld.name;
      marker.bindTooltip(tip);
      marker.on('click', () => { select.value = bld.id; getRoute(); });
    });

    if(navSimulate){
      // Desk testing: click the map to "walk" there
      navMap.on('click', e => navHandlePosition(
        { coords: { latitude: e.latlng.lat, longitude: e.latlng.lng, accuracy: 5 } }, true));
    }
  })();
  navInit.catch(() => { navInit = null; });
  return navInit;
}

// The map is inside a tab that starts hidden, so build it (and fix its size) when that tab opens.
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const mapEl = document.getElementById('campusMap');
    const view = mapEl && mapEl.closest('.view');
    if(view && view.id === btn.dataset.view){
      initNavigator()
        .then(() => setTimeout(() => navMap.invalidateSize(), 50))
        .catch(err => { console.error(err); navMessage('The map could not be loaded. Is the Flask server running?'); });
    }
  });
});
window.addEventListener('load', () => {
  const mapEl = document.getElementById('campusMap');
  if(mapEl && mapEl.offsetParent !== null) initNavigator().catch(console.error);
});

function navDrawUser(lat, lng, accuracy){
  const ll = [lat, lng];
  if(!navUser){
    navAccuracy = L.circle(ll, { radius: accuracy, weight: 0, fillColor: '#1a73e8', fillOpacity: 0.12 }).addTo(navMap);
    navUser = L.circleMarker(ll, {
      radius: 8, color: '#fff', weight: 3, fillColor: '#1a73e8', fillOpacity: 1, bubblingMouseEvents: false
    }).addTo(navMap);
  } else {
    navUser.setLatLng(ll);
    navAccuracy.setLatLng(ll).setRadius(accuracy);
  }
}

function navClearRoute(){
  if(navRouteLayer){ navRouteLayer.remove(); navRouteLayer = null; }
  if(navDestMarker){ navDestMarker.remove(); navDestMarker = null; }
}

function navStopTracking(){
  if(navWatchId !== null){ navigator.geolocation.clearWatch(navWatchId); navWatchId = null; }
}

function navDrawRoute(data, fit){
  if(navRouteLayer) navRouteLayer.remove();
  const blue = '#1a73e8';
  navRouteLayer = L.layerGroup([
    L.polyline(data.start_connector, { color: blue, weight: 3, dashArray: '6 8', opacity: 0.8 }),
    L.polyline(data.coordinates, { color: blue, weight: 6, opacity: 0.9 }),
    L.polyline(data.end_connector, { color: blue, weight: 3, dashArray: '6 8', opacity: 0.8 })
  ]).addTo(navMap);

  navEndSnap = data.end_connector[0];

  if(fit){
    navMap.fitBounds(
      L.latLngBounds([...data.coordinates, ...data.start_connector, ...data.end_connector]),
      { padding: [40, 40], maxZoom: 18 }
    );
  }

  let msg = `${navDest.name}: ${Math.round(data.distance_meters)} m · about ${Math.max(1, Math.round(data.estimated_walk_time_min))} min walk`;
  if(data.off_campus) msg += ` (you seem to be off campus, so this route starts at the ${data.start_name})`;
  else if(data.start_name !== 'your location') msg += ` (starting from the ${data.start_name})`;
  if(navNote) msg += ` ${navNote}`;
  navMessage(msg);
}

// Ask the server for a route from the user's position (or the main gate when pos is null).
async function navRequestRoute(pos, force){
  if(!navDest || (navBusy && !force)) return;
  const myId = ++navReqId;
  navBusy = true;
  try{
    const body = { destination_id: navDest.id };
    if(pos) body.start = { lat: pos.lat, lng: pos.lng };
    const res = await fetch('/api/route', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if(myId !== navReqId || !navDest) return;   // destination changed or cancelled meanwhile
    if(!res.ok){ navMessage(data.error || 'Could not calculate a route.'); return; }
    navDrawRoute(data, force);
  } catch(err){
    console.error(err);
    navMessage('Could not reach the navigation server. Is Flask running?');
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
  navMessage(`You have arrived at ${name}.`);
}

// Runs for the first GPS fix (force = true) and then for every watchPosition update.
function navHandlePosition(pos, force){
  const { latitude: lat, longitude: lng, accuracy } = pos.coords;
  navDrawUser(lat, lng, accuracy);
  if(!navDest) return;

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
  if(err.code === 1) return 'Location permission was denied. Allow location access for this site to get live directions.';
  if(err.code === 2) return 'Your location is not available right now.';
  return 'Getting your location took too long.';
}

// "Get Route" button / clicking a building on the map
async function getRoute(){
  const select = document.getElementById('toSelect');
  if(!select.value){
    navMessage('Please select a destination.');
    return;
  }
  try{
    await initNavigator();
  } catch(err){
    console.error(err);
    navMessage('The map could not be loaded. Is the Flask server running?');
    return;
  }
  const dest = navBuildings.get(select.value);
  if(!dest) return;

  navStopTracking();
  navClearRoute();
  navDest = dest;
  navEndSnap = null;
  navLast = null;
  navNote = '';
  navDestMarker = L.circleMarker([dest.lat, dest.lng], {
    radius: 12, color: '#c0392b', weight: 3, fillOpacity: 0, bubblingMouseEvents: false
  }).addTo(navMap);
  navMap.invalidateSize();

  if(navSimulate){
    navMessage('Simulation: starting at the main gate. Click the map to move.');
    navRequestRoute(null, true);
    return;
  }
  if(!('geolocation' in navigator)){
    navNote = 'This browser has no location support.';
    navRequestRoute(null, true);
    return;
  }

  navMessage('Getting your location…');
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
