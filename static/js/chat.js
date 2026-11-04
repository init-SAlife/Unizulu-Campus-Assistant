// AI Assistant: the chat list, messages, uploads and the mic.

// Chat list (each chat has its own memory, stored in the database)
let currentChatId = null;
let chats = [];
// bumped whenever a different chat is shown, so an answer that arrives
// after the student has switched chats is not drawn into the wrong chat
let viewToken = 0;

function setChatInUrl(chatId){
  if(currentView !== 'chatView') return;   // only the chat view shows a chat in the address bar
  // a guest chat can't be reopened from a link, so it never goes in the address bar
  const url = chatId && !isGuest ? `/?chat=${encodeURIComponent(chatId)}` : '/#chat';
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
  if(isGuest){
    tpl.querySelector('.welcome-howto').textContent =
      'Type your question or tap the mic to speak. Sign in to attach your academic record or proof of registration.';
  }
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
  let lastQuestion = null;
  data.messages.forEach(m => {
    const who = m.role === 'user' ? 'user' : 'bot';
    const row = appendMessage(m.content, who, false);
    if(who === 'user') lastQuestion = m.content.startsWith('📎') ? null : m.content;
    else{ addAnswerActions(row, m.content, lastQuestion); lastQuestion = null; }
  });
  const last = wrap.lastElementChild;
  if(last) last.scrollIntoView({block: 'end'});
  renderChatList();
}

// called after every answer/upload: remember which chat it went into,
// update its title (Claude names new chats) and move it to the top of the list
function afterReply(data, sentFromToken){
  if(data && data.guest) updateGuestAllowance(data.guest);
  if(!data || !data.chat_id) return;
  const stillViewing = sentFromToken === viewToken;
  if(stillViewing){
    currentChatId = data.chat_id;
    currentHandbook = data.handbook || null;
    setChatInUrl(currentChatId);
    setChatHeader(data.title, currentHandbook);
  }
  if(isGuest) return;          // guests have no saved chat list
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

// Chat list open / close
// Wide screens: the ☰ button shows or hides the chat list beside the chat (the choice is
// remembered on this browser).
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

// Chat (wired to Flask /chat -> Chatbot.py)
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

// Typing out an answer (top to bottom, like it's being written)
let activeTyping = null;   // the answer currently being typed, so it can be finished early

function finishTyping(){
  if(activeTyping) activeTyping.finish();
}

// opts.actions: show Copy / feedback / Share / Regenerate under the answer once it is typed;
// opts.question: the question it answers (used by Share and Regenerate)
function typeMessage(text, token, opts = {}){
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
    if(opts.actions) addAnswerActions(row, text, opts.question);
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

// "Thinking" bubble: only the five-bar loading animation is shown;
// the label ("Thinking", "Reading your document") is read out by screen readers only
function appendThinking(label){
  const wrap = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row bot thinking-row';
  row.innerHTML = `<div class="bubble thinking" role="status" aria-live="polite">
      <span class="sr-only">${escapeHtml(label)}</span>
      <span class="loading" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></span>
    </div>`;
  wrap.appendChild(row);
  wrap.scrollTop = wrap.scrollHeight;
  return row;
}

// guests who reach their limit: buttons to sign in or create an account
function appendSignInPrompt(){
  const wrap = document.getElementById('chatMessages');
  const row = document.createElement('div');
  row.className = 'msg-row bot';
  row.innerHTML = `<div class="bubble signin-prompt">
      <a class="home-btn" href="/login">Sign in</a>
      <a class="signin-prompt-alt" href="/login?mode=register">Create a free account</a>
    </div>`;
  wrap.appendChild(row);
  row.scrollIntoView({behavior:'smooth', block:'end'});
}

// Under every answer (outside the bubble): Copy, Good/Bad response,
// Download and Regenerate. Regenerate is only on the newest answer.
const ANS_ICONS = {
  copy: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><polyline points="20 6 9 17 4 12"></polyline></svg>',
  feedback: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 10v12"></path><path d="M15 5.88 14 10h5.83a2 2 0 0 1 1.92 2.56l-2.33 8A2 2 0 0 1 17.5 22H4a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2h2.76a2 2 0 0 0 1.79-1.11L12 2a3.13 3.13 0 0 1 3 3.88Z"></path><path d="M17 14V2"></path><path d="M9 18.12 10 14H4.17a2 2 0 0 1-1.92-2.56l2.33-8A2 2 0 0 1 6.5 2H20a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2h-2.76a2 2 0 0 0-1.79 1.11L12 22a3.13 3.13 0 0 1-3-3.88Z"></path></svg>',
  download: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 14v6a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-6"></path><polyline points="8 11 12 15 16 11"></polyline><line x1="12" y1="3" x2="12" y2="15"></line></svg>',
  regenerate: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path><path d="M3 3v5h5"></path></svg>',
};

function addAnswerActions(row, text, question){
  if(!row || row.querySelector('.answer-actions')) return;
  row._answer = text;
  row._question = question || null;
  row.classList.add('has-actions');
  const bar = document.createElement('div');
  bar.className = 'answer-actions';
  bar.innerHTML = `
    <button type="button" class="ans-btn" data-ans="copy" aria-label="Copy" title="Copy">${ANS_ICONS.copy}</button>
    <div class="ans-tooltip-wrap">
      <button type="button" class="ans-btn" data-ans="feedback" aria-label="Feedback" title="Rate this answer" aria-haspopup="menu" aria-expanded="false">${ANS_ICONS.feedback}</button>
      <div class="ans-tooltip" role="menu">
        <button type="button" class="ans-option" role="menuitem" data-rate="good">Good response</button>
        <button type="button" class="ans-option" role="menuitem" data-rate="bad">Bad response</button>
        <div class="ans-success" role="status">${ANS_ICONS.check}Submitted</div>
      </div>
    </div>
    <button type="button" class="ans-btn" data-ans="download" aria-label="Download" title="Download this answer">${ANS_ICONS.download}</button>
    <button type="button" class="ans-btn" data-ans="regenerate" aria-label="Regenerate" title="Regenerate this answer">${ANS_ICONS.regenerate}</button>`;
  row.appendChild(bar);
  updateRegenerateButtons();
}

// Regenerate: only on the newest answer, and only when it answered a question
function updateRegenerateButtons(){
  const rows = document.querySelectorAll('#chatMessages .msg-row.has-actions');
  rows.forEach((r, i) => {
    const btn = r.querySelector('[data-ans="regenerate"]');
    if(btn) btn.hidden = !(i === rows.length - 1 && r._question);
  });
}

function plainAnswer(text){ return String(text || '').replace(/\*\*(.+?)\*\*/g, '$1').trim(); }

async function copyText(text){
  try{ await navigator.clipboard.writeText(text); return true; }
  catch(err){
    // older browsers / http pages: copy through a hidden text box
    const box = document.createElement('textarea');
    box.value = text; box.setAttribute('readonly', ''); box.style.position = 'fixed'; box.style.opacity = '0';
    document.body.appendChild(box); box.select();
    let ok = false;
    try{ ok = document.execCommand('copy'); } catch(e){}
    box.remove();
    return ok;
  }
}

// a small label above a button for a moment ("Copied")
function flashLabel(btn, label){
  const old = btn.parentElement.querySelector('.ans-flash');
  if(old) old.remove();
  const tag = document.createElement('span');
  tag.className = 'ans-flash';
  tag.textContent = label;
  btn.insertAdjacentElement('afterend', tag);
  setTimeout(() => tag.remove(), 1600);
}

function closeFeedbackMenus(except){
  document.querySelectorAll('.ans-tooltip.show').forEach(t => {
    if(t === except) return;
    t.classList.remove('show');
    const b = t.parentElement.querySelector('[data-ans="feedback"]');
    b.classList.remove('active'); b.setAttribute('aria-expanded', 'false');
  });
}

document.getElementById('chatMessages').addEventListener('click', async event => {
  const row = event.target.closest('.msg-row.has-actions');
  if(!row) return;
  const option = event.target.closest('[data-rate]');
  if(option){ submitFeedback(row, option.dataset.rate); return; }
  const btn = event.target.closest('[data-ans]');
  if(!btn) return;
  const action = btn.dataset.ans;

  if(action === 'copy'){
    const ok = await copyText(plainAnswer(row._answer));
    btn.innerHTML = ok ? ANS_ICONS.check : ANS_ICONS.copy;
    btn.classList.toggle('done', ok);
    flashLabel(btn, ok ? 'Copied' : 'Could not copy');
    setTimeout(() => { btn.innerHTML = ANS_ICONS.copy; btn.classList.remove('done'); }, 1600);
  }
  else if(action === 'feedback'){
    event.stopPropagation();
    const tip = btn.parentElement.querySelector('.ans-tooltip');
    if(tip.classList.contains('submitted')) return;
    const open = !tip.classList.contains('show');
    closeFeedbackMenus(tip);
    tip.classList.toggle('show', open);
    btn.classList.toggle('active', open);
    btn.setAttribute('aria-expanded', open ? 'true' : 'false');
  }
  else if(action === 'download'){
    downloadAnswer(row);
    flashLabel(btn, 'Downloaded');
  }
  else if(action === 'regenerate'){
    const sendBtn = document.querySelector('.send-btn');
    if(sendBtn.disabled || !row._question) return;      // an answer is already on its way
    finishTyping();
    const question = row._question;
    row.remove();
    updateRegenerateButtons();
    await requestAnswer(question, true);
  }
});
document.addEventListener('click', event => {
  if(!event.target.closest('.ans-tooltip-wrap')) closeFeedbackMenus(null);
});
document.addEventListener('keydown', event => { if(event.key === 'Escape') closeFeedbackMenus(null); });

// Download: the question and the answer as a text file, e.g. campus-ai-answer-2026-10-24-0717.txt
function downloadAnswer(row){
  const now = new Date();
  const pad = n => String(n).padStart(2, '0');
  const stamp = `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
  const when = now.toLocaleString('en-ZA', {day: 'numeric', month: 'long', year: 'numeric', hour: '2-digit', minute: '2-digit'});
  const lines = ['UNIZULU Campus AI Assistant', `Saved: ${when}`, ''];
  if(row._question) lines.push('Question:', row._question, '');
  lines.push('Answer:', plainAnswer(row._answer), '');
  // "\ufeff" makes Notepad on Windows read letters such as é and the emoji correctly
  const blob = new Blob(['\ufeff' + lines.join('\r\n')], {type: 'text/plain;charset=utf-8'});
  const link = document.createElement('a');
  link.href = URL.createObjectURL(blob);
  link.download = `campus-ai-answer-${stamp}-${pad(now.getHours())}${pad(now.getMinutes())}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

async function submitFeedback(row, rating){
  const tip = row.querySelector('.ans-tooltip');
  const btn = row.querySelector('[data-ans="feedback"]');
  tip.classList.add('submitted');
  btn.classList.remove('rated-good', 'rated-bad');
  btn.classList.add(rating === 'good' ? 'rated-good' : 'rated-bad');
  btn.title = rating === 'good' ? 'You rated this a good response' : 'You rated this a bad response';
  try{
    await api('/chat/feedback', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({rating, answer: row._answer, question: row._question, chat_id: currentChatId}),
    });
  } catch(err){ console.error(err); }
  // like the design: show "Submitted", hide after 1.5 s, then reset the options
  setTimeout(() => {
    tip.classList.remove('show');
    btn.classList.remove('active');
    btn.setAttribute('aria-expanded', 'false');
    setTimeout(() => tip.classList.remove('submitted'), 250);
  }, 1500);
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

// Staged attachments: nothing is uploaded until the send button is pressed
// each staged file is {file, type}, type is 'academic_record' or 'proof_of_registration'
let pendingFiles = [];
const DOC_LABELS = {academic_record: 'Academic record', proof_of_registration: 'Proof of registration'};

function renderAttachmentPreview(){
  const wrap = document.getElementById('attachmentPreview');
  wrap.innerHTML = '';
  pendingFiles.forEach(({file, type}, i) => {
    const chip = document.createElement('div');
    chip.className = 'attachment-chip';
    const name = document.createElement('span');
    name.className = 'attachment-name';
    name.textContent = `📎 ${DOC_LABELS[type]}: ${file.name}`;
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

// Paperclip: first choose Academic record or Proof of registration, then the file
const uploadBtn = document.getElementById('uploadBtn');
const attachMenu = document.getElementById('attachMenu');

function setAttachMenu(open){
  attachMenu.hidden = !open;
  uploadBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
  uploadBtn.classList.toggle('open', open);
  if(open) attachMenu.querySelector('[data-doc-type]').focus();
}
uploadBtn.addEventListener('click', event => {
  event.stopPropagation();
  setAttachMenu(attachMenu.hidden);
});
attachMenu.querySelectorAll('[data-doc-type]').forEach(item => {
  item.addEventListener('click', () => {
    document.getElementById('documentType').value = item.dataset.docType;
    setAttachMenu(false);
    document.getElementById('fileInput').click();
  });
});
// clicking anywhere else, or Escape, closes the menu; arrow keys move between the two choices
document.addEventListener('click', event => {
  if(!attachMenu.hidden && !attachMenu.contains(event.target)) setAttachMenu(false);
});
attachMenu.addEventListener('keydown', event => {
  const items = Array.from(attachMenu.querySelectorAll('[data-doc-type]'));
  const i = items.indexOf(document.activeElement);
  if(event.key === 'Escape'){ setAttachMenu(false); uploadBtn.focus(); }
  else if(event.key === 'ArrowDown'){ event.preventDefault(); items[(i + 1) % items.length].focus(); }
  else if(event.key === 'ArrowUp'){ event.preventDefault(); items[(i - 1 + items.length) % items.length].focus(); }
});

document.getElementById('fileInput').addEventListener('change', event=>{
  const type = document.getElementById('documentType').value;
  pendingFiles = pendingFiles.concat(Array.from(event.target.files).map(file => ({file, type})));
  renderAttachmentPreview();
  event.target.value = '';
  document.getElementById('chatInput').focus();
});

async function uploadPendingFiles(staged){
  // one upload per document type, so an academic record and a proof of
  // registration attached together are each read as what they are
  for(const type of ['academic_record', 'proof_of_registration']){
    const files = staged.filter(s => s.type === type).map(s => s.file);
    if(files.length) await uploadFilesOfType(files, type);
  }
}

async function uploadFilesOfType(files, documentType){
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
    if(token === viewToken) typeMessage(data.message, token, {actions: true});
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
  await requestAnswer(val);
}

// asks the server and types the answer (sendChat, and Regenerate under the newest answer)
async function requestAnswer(val, regenerate = false){
  const input = document.getElementById('chatInput');
  const sendBtn = document.querySelector('.send-btn');
  const uploadBtn = document.getElementById('uploadBtn');
  input.disabled = true;
  sendBtn.disabled = true;
  uploadBtn.disabled = true;

  const token = viewToken;
  const thinkingRow = appendThinking('Thinking');

  try{
    const {res, data} = await api('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question: val, chat_id: currentChatId, regenerate })
    });

    thinkingRow.remove();

    if(!res.ok){
      if(data.guest) updateGuestAllowance(data.guest);
      if(token === viewToken){
        appendMessage(data.error || 'Something went wrong. Please try again.', 'bot');
        if(data.signin_prompt) appendSignInPrompt();
      }
    } else {
      if(token === viewToken){
        typeMessage(data.answer, token, {actions: true, question: val});
        // e.g. a guest asking to check their registration: offer to sign in
        if(data.signin_prompt){ finishTyping(); appendSignInPrompt(); }
      }
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
