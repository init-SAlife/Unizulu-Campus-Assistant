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

document.getElementById('logoutBtn').addEventListener('click', async ()=>{
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
  document.getElementById('userName').textContent = currentUser.full_name;
  document.getElementById('userInitial').textContent = currentUser.full_name.trim().charAt(0).toUpperCase();
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
