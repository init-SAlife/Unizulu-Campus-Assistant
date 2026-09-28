// ---------- Sign in / create account ----------
let mode = 'login';

const form = document.getElementById('authForm');
const errorBox = document.getElementById('authError');
const submitBtn = document.getElementById('authSubmit');
const passwordInput = document.getElementById('password');

function setMode(next){
  mode = next;
  const registering = mode === 'register';
  document.querySelectorAll('.auth-tab').forEach(tab => {
    const active = tab.dataset.mode === mode;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('.register-only').forEach(el => { el.hidden = !registering; });
  document.querySelectorAll('.login-only').forEach(el => { el.hidden = registering; });
  submitBtn.textContent = registering ? 'Create account' : 'Sign in';
  passwordInput.autocomplete = registering ? 'new-password' : 'current-password';
  passwordInput.placeholder = registering ? 'Choose a password' : 'Your password';
  document.getElementById('switchText').textContent = registering ? 'Already have an account?' : 'New here?';
  document.getElementById('switchMode').textContent = registering ? 'Sign in' : 'Create an account';
  showError('');
  (registering ? document.getElementById('fullName') : document.getElementById('email')).focus();
}

function showError(message){
  errorBox.textContent = message;
  errorBox.hidden = !message;
  if(message){ errorBox.classList.remove('shake'); void errorBox.offsetWidth; errorBox.classList.add('shake'); }
  if(message) document.getElementById('authNotice').hidden = true;
}

// "Forgotten password?" carries over the email that was already typed
document.getElementById('forgotLink').addEventListener('click', event => {
  const email = document.getElementById('email').value.trim();
  if(email){
    event.preventDefault();
    window.location.href = '/forgot-password?email=' + encodeURIComponent(email);
  }
});

document.querySelectorAll('.auth-tab').forEach(tab => tab.addEventListener('click', () => setMode(tab.dataset.mode)));
document.getElementById('switchMode').addEventListener('click', () => setMode(mode === 'login' ? 'register' : 'login'));

form.addEventListener('submit', async event => {
  event.preventDefault();
  const body = {
    email: document.getElementById('email').value.trim(),
    password: passwordInput.value,
  };
  if(mode === 'register'){
    body.full_name = document.getElementById('fullName').value.trim();
    if(!body.full_name){ showError('Please enter your full name.'); return; }
  }
  if(!body.email || !body.password){ showError('Please enter your email and password.'); return; }
  if(mode === 'register' && body.password.length < 8){ showError('Your password must be at least 8 characters.'); return; }

  submitBtn.disabled = true;
  submitBtn.classList.add('is-busy');
  showError('');
  try{
    const res = await fetch(mode === 'register' ? '/auth/register' : '/auth/login', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if(!res.ok){
      showError(data.error || 'Something went wrong. Please try again.');
      return;
    }
    window.location.href = '/';
  } catch(err){
    showError('Could not reach the server. Is the Flask server running?');
    console.error(err);
  } finally {
    submitBtn.disabled = false;
    submitBtn.classList.remove('is-busy');
  }
});

setMode('login');

// back from "Forgotten password?" after choosing a new password
if(new URLSearchParams(location.search).get('reset') === '1'){
  const notice = document.getElementById('authNotice');
  notice.textContent = 'Your password has been changed. Sign in with your new password.';
  notice.hidden = false;
  history.replaceState(null, '', '/login');
}
