// =====================================================================
// app.js -- shared by both parts of the page: signing out, switching
// between Home / AI Assistant / Campus Navigator, and start-up.
// The AI Assistant's code is in chat.js, the Campus Navigator's in navigation.js.
// Load order in index.html: app.js, chat.js, navigation.js.
// =====================================================================

// ---------- Views: Home, AI Assistant, Campus Navigator ----------
// The address bar follows the view, so refreshing the page stays where you were:
//   /            Home
//   /#chat       AI Assistant (new chat)    /?chat=<id>  AI Assistant (that chat)
//   /#navigate   Campus Navigator
let currentView = 'homeView';

function showView(viewId){
  currentView = viewId;
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.toggle('active', b.dataset.view === viewId));
  document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === viewId));
  if(viewId === 'chatView'){
    setChatInUrl(currentChatId);
    document.getElementById('chatInput').focus();
  } else {
    if(viewId === 'navView') onNavigatorShown();
    history.replaceState(null, '', viewId === 'navView' ? '/#navigate' : '/');
    document.title = 'UNIZULU Campus AI Assistant';
  }
  window.scrollTo(0, 0);
}

document.querySelectorAll('.tab-btn').forEach(btn=>{
  btn.addEventListener('click', ()=> showView(btn.dataset.view));
});
// the two buttons on the Home screen
document.querySelectorAll('[data-go]').forEach(btn=>{
  btn.addEventListener('click', ()=> showView(btn.dataset.go));
});

// ---------- Signed-in user and API calls ----------
let currentUser = null;

// every call to the backend goes through here: if the sign-in has expired
// (401), go back to the login page instead of showing a broken chat
async function api(url, options = {}){
  const res = await fetch(url, {credentials: 'same-origin', ...options});
  if(res.status === 401){
    window.location.href = '/login';
    throw new Error('signed out');
  }
  const data = await res.json().catch(() => ({}));
  return {res, data};
}

// ---------- Account card (profile button in the header) ----------
const accountOverlay = document.getElementById('accountOverlay');
const profileBtn = document.getElementById('profileBtn');

function fillProfile(user){
  const name = (user.full_name || '').trim();
  const initial = name.charAt(0).toUpperCase() || '?';
  document.getElementById('profileInitial').textContent = initial;
  document.getElementById('acctAvatar').textContent = initial;
  document.getElementById('acctName').textContent = name || 'Student';
  document.getElementById('detailName').textContent = name || 'Student';
  profileBtn.title = name ? `${name} · your account` : 'Your account';
  if(user.email){
    document.getElementById('detailEmail').textContent = user.email;
    document.getElementById('detailEmailRow').hidden = false;
  }
}

// "Profile" row (and the "Update Profile" button) show the student's details
const profileRow = document.getElementById('profileRow');
function toggleProfileDetails(show){
  const details = document.getElementById('profileDetails');
  const open = show === undefined ? details.hidden : show;
  details.hidden = !open;
  profileRow.setAttribute('aria-expanded', open ? 'true' : 'false');
  profileRow.classList.toggle('open', open);
}
profileRow.addEventListener('click', () => toggleProfileDetails());
document.getElementById('updateProfileBtn').addEventListener('click', () => toggleProfileDetails(true));

function openAccount(){
  accountOverlay.classList.remove('closing');
  accountOverlay.hidden = false;
  profileBtn.setAttribute('aria-expanded', 'true');
  syncThemeSwitch();
  document.getElementById('acctClose').focus();
}
function closeAccount(){
  if(accountOverlay.hidden || accountOverlay.classList.contains('closing')) return;
  // play the slide-out, then hide
  accountOverlay.classList.add('closing');
  const done = () => {
    accountOverlay.classList.remove('closing');
    accountOverlay.hidden = true;
    toggleProfileDetails(false);
    togglePasswordPanel(false);
  };
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if(reduced) done(); else setTimeout(done, 180);
  profileBtn.setAttribute('aria-expanded', 'false');
  profileBtn.focus();
}

// ---------- Settings: appearance (light / dark) ----------
function syncThemeSwitch(){
  const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
  document.querySelectorAll('.theme-opt').forEach(btn => {
    btn.setAttribute('aria-checked', btn.dataset.themeChoice === theme ? 'true' : 'false');
  });
  document.querySelector('.theme-switch').dataset.value = theme;
}
document.querySelectorAll('.theme-opt').forEach(btn => {
  btn.addEventListener('click', () => {
    // a short cross-fade so the whole page changes colour smoothly
    document.documentElement.classList.add('theme-changing');
    setTheme(btn.dataset.themeChoice);
    syncThemeSwitch();
    setTimeout(() => document.documentElement.classList.remove('theme-changing'), 400);
  });
});
syncThemeSwitch();

// ---------- Settings: change password ----------
const passwordRow = document.getElementById('passwordRow');
const passwordPanel = document.getElementById('passwordPanel');
const passwordForm = document.getElementById('changePasswordForm');

function togglePasswordPanel(show){
  const open = show === undefined ? passwordPanel.hidden : show;
  passwordPanel.hidden = !open;
  passwordRow.setAttribute('aria-expanded', open ? 'true' : 'false');
  passwordRow.classList.toggle('open', open);
  if(!open){
    passwordForm.reset();
    showPasswordMessage('', '');
  } else {
    setTimeout(() => document.getElementById('currentPassword').focus(), 50);
  }
}
passwordRow.addEventListener('click', () => togglePasswordPanel());

function showPasswordMessage(error, success){
  const err = document.getElementById('passwordError');
  const ok = document.getElementById('passwordSuccess');
  err.textContent = error; err.hidden = !error;
  ok.textContent = success; ok.hidden = !success;
  if(error){ err.classList.remove('shake'); void err.offsetWidth; err.classList.add('shake'); }
}

passwordForm.addEventListener('submit', async event => {
  event.preventDefault();
  const current = document.getElementById('currentPassword').value;
  const next = document.getElementById('newPasswordSetting').value;
  const again = document.getElementById('confirmPasswordSetting').value;
  if(!current){ showPasswordMessage('Please enter your current password.', ''); return; }
  if(next.length < 8){ showPasswordMessage('Your new password must be at least 8 characters.', ''); return; }
  if(next !== again){ showPasswordMessage('The two new passwords do not match.', ''); return; }

  const btn = passwordForm.querySelector('button[type="submit"]');
  btn.disabled = true; btn.classList.add('is-busy');
  showPasswordMessage('', '');
  try{
    const {res, data} = await api('/auth/password/change', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({current_password: current, new_password: next}),
    });
    if(!res.ok){ showPasswordMessage(data.error || 'Your password could not be changed.', ''); return; }
    passwordForm.reset();
    showPasswordMessage('', 'Password updated. Any other devices signed in to your account have been signed out.');
  } catch(err){
    if(err.message !== 'signed out') showPasswordMessage('Could not reach the server. Please try again.', '');
  } finally {
    btn.disabled = false; btn.classList.remove('is-busy');
  }
});

profileBtn.addEventListener('click', openAccount);
document.getElementById('acctClose').addEventListener('click', closeAccount);
// clicking the dark area outside the card closes it
accountOverlay.addEventListener('click', e => { if(e.target === accountOverlay) closeAccount(); });
document.addEventListener('keydown', e => { if(e.key === 'Escape' && !accountOverlay.hidden) closeAccount(); });

document.getElementById('signOutBtn').addEventListener('click', async ()=>{
  if(!confirm('Are you sure you want to sign out?')) return;
  try{ await fetch('/auth/logout', {method: 'POST', credentials: 'same-origin'}); }
  finally{ window.location.href = '/login'; }
});

// ---------- Start-up: who is signed in, their chats, and which chat to show ----------
async function boot(){
  try{
    const {data} = await api('/auth/me');
    currentUser = data.user;
  } catch(err){
    return;   // not signed in: api() is already sending the browser to /login
  }
  fillProfile(currentUser);
  const firstName = currentUser.full_name.trim().split(' ')[0];
  document.getElementById('homeName').textContent = firstName ? `, ${firstName}` : '';

  // after signing in the Home screen asks what to use; a link to a chat
  // (/?chat=...) or to a view (/#chat, /#navigate) opens that directly
  const chatFromUrl = new URLSearchParams(window.location.search).get('chat');
  showViewForAddress(chatFromUrl);

  await loadChatList();
  if(chatFromUrl) await openChat(chatFromUrl);
  else newChat();
}

function showViewForAddress(chatFromUrl){
  const hash = window.location.hash;
  if(chatFromUrl || hash === '#chat') showView('chatView');
  else if(hash === '#navigate') showView('navView');
  else showView('homeView');
}
// typing /#chat or /#navigate into the address bar (without reloading) switches view too
window.addEventListener('hashchange', ()=> showViewForAddress(null));

// start once all three scripts (app.js, chat.js, navigation.js) have loaded
window.addEventListener('DOMContentLoaded', boot);
