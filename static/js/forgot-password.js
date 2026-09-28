// =====================================================================
// forgot-password.js -- "Forgotten password?" in three steps:
//   1. email   -> POST /auth/password/forgot   (a 6-digit code is emailed)
//   2. code    -> POST /auth/password/verify   (gives a one-time reset token)
//   3. new pw  -> POST /auth/password/reset
// =====================================================================

const reset = { email: '', token: '', step: 1 };
let resendTimer = null;

async function postJSON(url, body){
  const res = await fetch(url, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    credentials: 'same-origin',
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  return {res, data};
}

function screen(n){ return document.getElementById('step' + n); }

function showError(n, message){
  const box = screen(n).querySelector('.auth-error');
  box.textContent = message || '';
  box.hidden = !message;
  if(message){
    // small shake so the student notices
    box.classList.remove('shake'); void box.offsetWidth; box.classList.add('shake');
  }
}

function busy(n, on, label){
  const btn = screen(n).querySelector('button[type="submit"]');
  if(!btn) return;
  if(on){ btn.dataset.label = btn.textContent; btn.textContent = label; }
  else if(btn.dataset.label){ btn.textContent = btn.dataset.label; }
  btn.disabled = on;
  btn.classList.toggle('is-busy', on);
}

// slides to step n (forwards slides left, backwards slides right)
function goTo(n){
  const from = reset.step;
  if(n === from) return;
  const back = n < from;
  const oldScreen = screen(from), newScreen = screen(n);
  oldScreen.classList.remove('active', 'enter-back');
  newScreen.classList.remove('enter-back');
  if(back) newScreen.classList.add('enter-back');
  newScreen.classList.add('active');
  reset.step = n;

  document.querySelectorAll('.reset-steps li').forEach(li => {
    const s = Number(li.dataset.step);
    li.classList.toggle('active', s === Math.min(n, 3));
    li.classList.toggle('done', s < n);
  });

  const first = newScreen.querySelector('input');
  if(first) setTimeout(() => first.focus(), 60);
}

// ---------- Step 1: email ----------
async function sendCode(isResend){
  const email = isResend ? reset.email : document.getElementById('resetEmail').value.trim();
  const n = isResend ? 2 : 1;
  if(!email || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email)){
    showError(1, 'Please enter a valid email address.');
    return;
  }
  showError(n, '');
  if(!isResend) busy(1, true, 'Sending…');
  else document.getElementById('resendBtn').disabled = true;

  try{
    const {res, data} = await postJSON('/auth/password/forgot', {email});
    if(!res.ok){
      showError(n, data.error || 'Something went wrong. Please try again.');
      if(isResend) document.getElementById('resendBtn').disabled = false;
      return;
    }
    reset.email = email;
    document.getElementById('sentTo').textContent = email;
    document.getElementById('codeMinutes').textContent = data.minutes || 10;
    clearCode();
    startResendCountdown(data.resend_after || 60);
    if(isResend) showError(2, '');
    goTo(2);
    if(isResend) flashNote('A new code is on its way.');
  } catch(err){
    showError(n, 'Could not reach the server. Please check your connection.');
    if(isResend) document.getElementById('resendBtn').disabled = false;
  } finally {
    if(!isResend) busy(1, false);
  }
}

function startResendCountdown(seconds){
  const btn = document.getElementById('resendBtn');
  const wait = document.getElementById('resendWait');
  clearInterval(resendTimer);
  let left = seconds;
  btn.disabled = true;
  const tick = () => {
    if(left <= 0){
      clearInterval(resendTimer);
      btn.disabled = false;
      wait.textContent = '';
      return;
    }
    wait.textContent = `(in ${left}s)`;
    left--;
  };
  tick();
  resendTimer = setInterval(tick, 1000);
}

function flashNote(text){
  const box = screen(2).querySelector('.reset-text');
  const note = document.createElement('p');
  note.className = 'reset-flash';
  note.textContent = text;
  box.after(note);
  setTimeout(() => note.remove(), 3500);
}

screen(1).addEventListener('submit', e => { e.preventDefault(); sendCode(false); });
document.getElementById('resendBtn').addEventListener('click', () => sendCode(true));
document.getElementById('changeEmailBtn').addEventListener('click', () => {
  document.getElementById('resetEmail').value = reset.email;
  showError(1, '');
  goTo(1);
});

// ---------- Step 2: the 6 code boxes ----------
const boxes = Array.from(document.querySelectorAll('.otp-box'));

function codeValue(){ return boxes.map(b => b.value).join(''); }
function clearCode(){ boxes.forEach(b => { b.value = ''; b.classList.remove('filled', 'wrong'); }); }

function fillFrom(index, digits){
  digits.split('').forEach((d, i) => {
    const box = boxes[index + i];
    if(box){ box.value = d; box.classList.add('filled'); }
  });
  const next = boxes[Math.min(index + digits.length, boxes.length - 1)];
  next.focus();
  if(codeValue().length === 6) screen(2).requestSubmit();
}

boxes.forEach((box, i) => {
  box.addEventListener('input', () => {
    const digits = box.value.replace(/\D/g, '');
    box.value = '';
    box.classList.remove('filled', 'wrong');
    if(digits) fillFrom(i, digits.slice(0, 6 - i));   // typing, or the phone's "paste code" suggestion
  });
  box.addEventListener('keydown', e => {
    if(e.key === 'Backspace' && !box.value && i > 0){
      boxes[i - 1].value = '';
      boxes[i - 1].classList.remove('filled');
      boxes[i - 1].focus();
      e.preventDefault();
    }
    if(e.key === 'ArrowLeft' && i > 0) boxes[i - 1].focus();
    if(e.key === 'ArrowRight' && i < boxes.length - 1) boxes[i + 1].focus();
  });
  box.addEventListener('paste', e => {
    const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 6);
    if(!digits) return;
    e.preventDefault();
    boxes.forEach(b => { b.value = ''; b.classList.remove('filled', 'wrong'); });
    fillFrom(0, digits);
  });
  box.addEventListener('focus', () => box.select());
});

screen(2).addEventListener('submit', async e => {
  e.preventDefault();
  const code = codeValue();
  if(code.length !== 6){ showError(2, 'Please enter all 6 digits.'); return; }
  showError(2, '');
  busy(2, true, 'Checking…');
  try{
    const {res, data} = await postJSON('/auth/password/verify', {email: reset.email, code});
    if(!res.ok){
      showError(2, data.error || 'That code is not correct.');
      boxes.forEach(b => b.classList.add('wrong'));
      setTimeout(() => { clearCode(); boxes[0].focus(); }, 450);
      return;
    }
    reset.token = data.reset_token;
    clearInterval(resendTimer);
    goTo(3);
  } catch(err){
    showError(2, 'Could not reach the server. Please check your connection.');
  } finally {
    busy(2, false);
  }
});

// ---------- Step 3: new password ----------
screen(3).addEventListener('submit', async e => {
  e.preventDefault();
  const pw = document.getElementById('newPassword').value;
  const again = document.getElementById('confirmPassword').value;
  if(pw.length < 8){ showError(3, 'Your password must be at least 8 characters.'); return; }
  if(pw !== again){ showError(3, 'The two passwords do not match.'); return; }
  showError(3, '');
  busy(3, true, 'Saving…');
  try{
    const {res, data} = await postJSON('/auth/password/reset', {email: reset.email, reset_token: reset.token, new_password: pw});
    if(!res.ok){ showError(3, data.error || 'Your password could not be changed.'); return; }
    reset.token = '';
    goTo(4);
  } catch(err){
    showError(3, 'Could not reach the server. Please check your connection.');
  } finally {
    busy(3, false);
  }
});

// coming from the sign-in page with the email already typed in
const emailFromLink = new URLSearchParams(location.search).get('email');
if(emailFromLink) document.getElementById('resetEmail').value = emailFromLink;
document.getElementById('resetEmail').focus();
