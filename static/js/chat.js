// =====================================================================
// chat.js -- AI Assistant: the chat list, messages, uploads and the mic.
// Talks to chatbot_routes.py (/chat, /upload, /chats ...).
// Uses api(), currentUser and currentView from app.js.
// =====================================================================

// ---------- Chat list (each chat has its own memory, stored in the database) ----------
let currentChatId = null;
let chats = [];
// bumped whenever a different chat is shown, so an answer that arrives
// after the student has switched chats is not drawn into the wrong chat
let viewToken = 0;

function setChatInUrl(chatId){
  if(currentView !== 'chatView') return;   // only the chat view shows a chat in the address bar
  const url = chatId ? `/?chat=${encodeURIComponent(chatId)}` : '/#chat';
  history.replaceState(null, '', url);
}

function setChatHeader(title, handbook){
  document.getElementById('chatTitle').textContent = title || 'New chat';
  if(currentView === 'chatView') document.title = (title ? title + ' · ' : '') + 'UNIZULU Campus AI Assistant';
  const chip = document.getElementById('handbookChip');
  if(handbook){
    chip.textContent = `${handbook.year} handbook`;
    chip.title = handbook.label;
    chip.hidden = false;
  } else {
    chip.hidden = true;
  }
}

function dayGroup(isoDate){
  const d = new Date(isoDate);
  const today = new Date(); today.setHours(0,0,0,0);
  const day = new Date(d); day.setHours(0,0,0,0);
  const diffDays = Math.round((today - day) / 86400000);
  if(diffDays <= 0) return 'Today';
  if(diffDays === 1) return 'Yesterday';
  if(diffDays < 7) return 'Previous 7 days';
  return 'Older';
}

function renderChatList(){
  const list = document.getElementById('chatList');
  list.innerHTML = '';
  document.getElementById('chatListEmpty').hidden = chats.length > 0;

  let lastGroup = null;
  chats.forEach(chat => {
    const group = dayGroup(chat.updated_at);
    if(group !== lastGroup){
      const heading = document.createElement('div');
      heading.className = 'chat-group';
      heading.textContent = group;
      list.appendChild(heading);
      lastGroup = group;
    }

    const item = document.createElement('div');
    item.className = 'chat-item' + (chat.id === currentChatId ? ' active' : '');
    item.dataset.id = chat.id;

    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'chat-item-title';
    open.textContent = chat.title || 'New chat';      // textContent: titles are never treated as HTML
    open.title = chat.title || 'New chat';
    open.addEventListener('click', () => openChat(chat.id));

    const rename = document.createElement('button');
    rename.type = 'button';
    rename.className = 'chat-item-action';
    rename.textContent = '✎';
    rename.setAttribute('aria-label', 'Rename chat');
    rename.title = 'Rename';
    rename.addEventListener('click', event => { event.stopPropagation(); startRename(item, chat); });

    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'chat-item-action danger';
    remove.textContent = '🗑';
    remove.setAttribute('aria-label', 'Delete chat');
    remove.title = 'Delete';
    remove.addEventListener('click', event => { event.stopPropagation(); deleteChat(chat); });

    item.append(open, rename, remove);
    list.appendChild(item);
  });
}

async function loadChatList(){
  const {res, data} = await api('/chats');
  if(res.ok){
    chats = data.chats || [];
    renderChatList();
  }
}

function startRename(item, chat){
  const input = document.createElement('input');
  input.className = 'chat-rename-input';
  input.value = chat.title || '';
  input.maxLength = 80;
  input.setAttribute('aria-label', 'New chat name');
  item.replaceChildren(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async save => {
    if(done) return;
    done = true;
    const title = input.value.trim();
    if(save && title && title !== chat.title){
      const {res, data} = await api(`/chats/${chat.id}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({title}),
      });
      if(res.ok){
        chat.title = data.title;
        if(chat.id === currentChatId) setChatHeader(chat.title, currentHandbook);
      }
    }
    renderChatList();
  };
  input.addEventListener('keydown', event => {
    if(event.key === 'Enter') finish(true);
    if(event.key === 'Escape') finish(false);
  });
  input.addEventListener('blur', () => finish(true));
}

async function deleteChat(chat){
  if(!confirm(`Delete "${chat.title || 'New chat'}"? This can't be undone.`)) return;
  const {res} = await api(`/chats/${chat.id}`, {method: 'DELETE'});
  if(res.ok){
    chats = chats.filter(c => c.id !== chat.id);
    if(chat.id === currentChatId) newChat();
    else renderChatList();
  }
}

let currentHandbook = null;

function showWelcome(){
  const tpl = document.getElementById('welcomeTemplate').content.cloneNode(true);
  const first = currentUser && currentUser.full_name ? currentUser.full_name.split(' ')[0] : '';
  tpl.querySelector('.welcome-name').textContent = first ? `, ${first}` : '';
  document.getElementById('chatMessages').appendChild(tpl);
}

function newChat(){
  viewToken++;
  currentChatId = null;
  currentHandbook = null;
  setChatInUrl(null);
  setChatHeader(null, null);
  document.getElementById('chatMessages').innerHTML = '';
  showWelcome();
  renderChatList();
  closeDrawer();
  if(currentView === 'chatView') document.getElementById('chatInput').focus();
}

async function openChat(chatId){
  const token = ++viewToken;
  closeDrawer();
  const {res, data} = await api(`/chats/${encodeURIComponent(chatId)}`);
  if(token !== viewToken) return;           // the student already switched to another chat
  if(!res.ok){
    newChat();
    return;
  }
  currentChatId = data.chat_id;
  currentHandbook = data.handbook;
  setChatInUrl(currentChatId);
  setChatHeader(data.title, data.handbook);
  const wrap = document.getElementById('chatMessages');
  wrap.innerHTML = '';
  if(!data.messages.length) showWelcome();
  data.messages.forEach(m => appendMessage(m.content, m.role === 'user' ? 'user' : 'bot', false));
  const last = wrap.lastElementChild;
  if(last) last.scrollIntoView({block: 'end'});
  renderChatList();
}

// called after every answer/upload: remember which chat it went into,
// update its title (Claude names new chats) and move it to the top of the list
function afterReply(data, sentFromToken){
  if(!data || !data.chat_id) return;
  const stillViewing = sentFromToken === viewToken;
  if(stillViewing){
    currentChatId = data.chat_id;
    currentHandbook = data.handbook || null;
    setChatInUrl(currentChatId);
    setChatHeader(data.title, currentHandbook);
  }
  const now = new Date().toISOString();
  const existing = chats.find(c => c.id === data.chat_id);
  if(existing){
    existing.title = data.title;
    existing.updated_at = now;
    chats = [existing, ...chats.filter(c => c.id !== data.chat_id)];
  } else {
    chats = [{id: data.chat_id, title: data.title, created_at: now, updated_at: now}, ...chats];
  }
  renderChatList();
}

document.getElementById('newChatBtn').addEventListener('click', newChat);

// ---------- Chat list open / close ----------
// Wide screens: the ☰ button shows or hides the chat list beside the chat
// (the choice is remembered on this browser). Small screens: the chat list
// slides in over the conversation and closes again after picking a chat.
const smallScreen = window.matchMedia('(max-width: 900px)');
const SIDEBAR_KEY = 'campusAI.sidebarHidden';

function readSidebarHidden(){
  try{ return localStorage.getItem(SIDEBAR_KEY) === '1'; } catch(err){ return false; }
}
function saveSidebarHidden(hidden){
  try{ localStorage.setItem(SIDEBAR_KEY, hidden ? '1' : '0'); } catch(err){ /* storage unavailable: just don't remember */ }
}

function updateToggleButton(){
  const btn = document.getElementById('sidebarToggle');
  const shown = smallScreen.matches
    ? document.getElementById('chatSidebar').classList.contains('open')
    : !document.getElementById('chatView').classList.contains('sidebar-collapsed');
  btn.setAttribute('aria-expanded', shown ? 'true' : 'false');
  btn.setAttribute('aria-label', shown ? 'Hide your chats' : 'Show your chats');
  btn.title = shown ? 'Hide chats' : 'Show chats';
}

function openDrawer(){
  document.getElementById('chatSidebar').classList.add('open');
  document.getElementById('sidebarBackdrop').classList.add('show');
  updateToggleButton();
}
function closeDrawer(){
  document.getElementById('chatSidebar').classList.remove('open');
  document.getElementById('sidebarBackdrop').classList.remove('show');
  updateToggleButton();
}

function toggleSidebar(){
  if(smallScreen.matches){
    if(document.getElementById('chatSidebar').classList.contains('open')) closeDrawer();
    else openDrawer();
    return;
  }
  const hidden = document.getElementById('chatView').classList.toggle('sidebar-collapsed');
  saveSidebarHidden(hidden);
  updateToggleButton();
}

document.getElementById('chatView').classList.toggle('sidebar-collapsed', readSidebarHidden());
document.getElementById('sidebarToggle').addEventListener('click', toggleSidebar);
document.getElementById('sidebarBackdrop').addEventListener('click', closeDrawer);
smallScreen.addEventListener('change', ()=>{ closeDrawer(); updateToggleButton(); });
updateToggleButton();

// ---------- Chat (wired to Flask /chat -> Chatbot.py) ----------
function escapeHtml(text){
  return String(text).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

// message text is always escaped first, so nothing a student types (or an
// answer contains) can run as HTML; then **bold** and line breaks are shown
function formatMessage(text){
  const safe = escapeHtml(text).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  return safe.split(/\n{2,}/).map(part => `<p>${part.replace(/\n/g, '<br>')}</p>`).join('');
}

// while an answer is being typed out, a "**" that has been opened but not
// yet closed would show as two stars -- hide it until its partner arrives
function formatPartial(text){
  const pairs = (text.match(/\*\*/g) || []).length;
  if(pairs % 2 === 1){
    const i = text.lastIndexOf('**');
    text = text.slice(0, i) + text.slice(i + 2);
  }
  return formatMessage(text);
}

// is the student reading the newest message? (then new text keeps it in view)
function nearBottom(el){ return el.scrollHeight - el.scrollTop - el.clientHeight < 90; }

// ---------- Typing out an answer (top to bottom, like it's being written) ----------
let activeTyping = null;   // the answer currently being typed, so it can be finished early

function finishTyping(){
  if(activeTyping) activeTyping.finish();
}

function typeMessage(text, token){
  finishTyping();
  const wrap = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row bot typing';
  const bubble = document.createElement('div');
  bubble.className = 'bubble';
  row.appendChild(bubble);
  wrap.appendChild(row);

  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  // speed: short answers ~80 characters a second; long ones finish in about 5 seconds
  const perSecond = Math.max(80, text.length / 5);
  const start = performance.now();
  let shown = 0, frame = null, finished = false;

  const render = count => {
    const follow = nearBottom(wrap);
    const done = count >= text.length;
    bubble.innerHTML = done ? formatMessage(text) : formatPartial(text.slice(0, count)) ;
    if(!done){
      // blinking cursor at the end of the text
      const caret = document.createElement('span');
      caret.className = 'type-caret';
      (bubble.lastElementChild || bubble).appendChild(caret);
    }
    if(follow) wrap.scrollTop = wrap.scrollHeight;
  };

  const finish = () => {
    if(finished) return;
    finished = true;
    cancelAnimationFrame(frame);
    render(text.length);
    row.classList.remove('typing');
    if(activeTyping && activeTyping.row === row) activeTyping = null;
  };

  const step = now => {
    if(token !== viewToken){ finish(); return; }     // the student switched chats
    // move forward to the next word boundary so words appear whole
    let target = Math.floor((now - start) / 1000 * perSecond);
    while(target < text.length && !/\s/.test(text[target])) target++;
    if(target > shown){ shown = target; render(shown); }
    if(shown >= text.length) finish();
    else frame = requestAnimationFrame(step);
  };

  activeTyping = {row, finish};
  if(reduced || !text) finish();
  else {
    wrap.scrollTop = wrap.scrollHeight;
    frame = requestAnimationFrame(step);
  }
  return row;
}

// "Thinking" bubble with three bouncing dots
function appendThinking(label){
  const wrap = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row bot thinking-row';
  row.innerHTML = `<div class="bubble thinking" role="status" aria-live="polite">
      <span class="thinking-label">${escapeHtml(label)}</span>
      <span class="dots" aria-hidden="true"><span></span><span></span><span></span></span>
    </div>`;
  wrap.appendChild(row);
  wrap.scrollTop = wrap.scrollHeight;
  return row;
}

function appendMessage(text, who, scroll = true){
  const wrap = document.getElementById('chatMessages');
  const welcome = wrap.querySelector('.welcome');
  if(welcome && who === 'user') welcome.remove();
  const row = document.createElement('div');
  row.className = 'msg-row ' + who;
  row.innerHTML = `<div class="bubble">${formatMessage(text)}</div>`;
  wrap.appendChild(row);
  if(scroll) row.scrollIntoView({behavior:'smooth', block:'end'});
  return row;
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
    const name = document.createElement('span');
    name.className = 'attachment-name';
    name.textContent = `📎 ${file.name}`;
    const remove = document.createElement('button');
    remove.type = 'button';
    remove.className = 'attachment-remove';
    remove.setAttribute('aria-label', `Remove ${file.name}`);
    remove.textContent = '×';
    remove.addEventListener('click', () => {
      pendingFiles.splice(i, 1);
      renderAttachmentPreview();
    });
    chip.append(name, remove);
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
  const label = documentType === 'academic_record' ? 'academic record' : 'proof of registration';
  const formData = new FormData();
  formData.append('document_type', documentType);
  if(currentChatId) formData.append('chat_id', currentChatId);
  files.forEach(file => formData.append('files', file));

  const token = viewToken;
  appendMessage(`📎 Uploaded ${label}: ${files.map(f => f.name).join(', ')}`, 'user');
  const thinkingRow = appendThinking('Reading your document');
  try{
    const {res, data} = await api('/upload', {method:'POST', body:formData});
    thinkingRow.remove();
    if(!res.ok){
      if(token === viewToken) appendMessage(data.error || 'The document could not be processed.', 'bot');
      return false;
    }
    if(token === viewToken) typeMessage(data.message, token);
    afterReply(data, token);
    return true;
  } catch(err){
    thinkingRow.remove();
    if(err.message !== 'signed out') appendMessage('Could not reach the document processing backend.', 'bot');
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

  finishTyping();
  appendMessage(val, 'user');
  input.value = '';

  const token = viewToken;
  const thinkingRow = appendThinking('Thinking');

  try{
    const {res, data} = await api('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: val, chat_id: currentChatId })
    });

    thinkingRow.remove();

    if(!res.ok){
      if(token === viewToken) appendMessage(data.error || 'Something went wrong. Please try again.', 'bot');
    } else {
      if(token === viewToken) typeMessage(data.answer, token);
      afterReply(data, token);
    }
  } catch(err){
    thinkingRow.remove();
    if(err.message !== 'signed out') appendMessage('Could not reach the assistant backend. Is the Flask server running?', 'bot');
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
