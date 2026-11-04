// loaded in the <head> of EVERY page (sign-in, forgot password, main app)

// Theme (light / dark)
const THEME_KEY = 'campusAI.theme';

function getSavedTheme(){
  try{ return localStorage.getItem(THEME_KEY) === 'dark' ? 'dark' : 'light'; }
  catch(err){ return 'light'; }
}

function applyTheme(theme){
  document.documentElement.setAttribute('data-theme', theme === 'dark' ? 'dark' : 'light');
}

function setTheme(theme){
  applyTheme(theme);
  try{ localStorage.setItem(THEME_KEY, theme); } catch(err){ /* can't remember it: still applied for now */ }
}

applyTheme(getSavedTheme());   // runs straight away, before the page is shown

// another tab changed the theme: follow it
window.addEventListener('storage', e => { if(e.key === THEME_KEY) applyTheme(getSavedTheme()); });


// Show / hide password
const EYE_OPEN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7S1 12 1 12z"/><circle cx="12" cy="12" r="3"/></svg>';
const EYE_SHUT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 19c-7 0-11-7-11-7a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 7 11 7a18.5 18.5 0 0 1-2.16 3.19"/><path d="M14.12 14.12a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>';

// wraps the input and adds the eye button after it (only once per input)
function addPasswordToggle(input){
  if(input.dataset.pwToggle) return;
  input.dataset.pwToggle = '1';

  const wrap = document.createElement('span');
  wrap.className = 'pw-wrap';
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.tabIndex = -1;          // Tab goes from the password straight on, the eye is for clicking
  btn.className = 'pw-toggle';
  wrap.appendChild(btn);

  const show = visible => {
    input.type = visible ? 'text' : 'password';
    btn.innerHTML = visible ? EYE_SHUT : EYE_OPEN;
    btn.setAttribute('aria-pressed', visible ? 'true' : 'false');
    btn.setAttribute('aria-label', visible ? 'Hide password' : 'Show password');
    btn.title = visible ? 'Hide password' : 'Show password';
  };
  show(false);

  // mousedown: keep the cursor in the box (and where it was) while toggling
  btn.addEventListener('mousedown', e => e.preventDefault());
  btn.addEventListener('click', () => {
    const start = input.selectionStart, end = input.selectionEnd;
    show(input.type === 'password');
    input.focus();
    try{ input.setSelectionRange(start, end); } catch(err){ /* not supported: fine */ }
  });

  // after a form is sent the password is hidden again
  if(input.form) input.form.addEventListener('submit', () => show(false));
}

function addPasswordToggles(root = document){
  root.querySelectorAll('input[type="password"]').forEach(addPasswordToggle);
}

document.addEventListener('DOMContentLoaded', () => addPasswordToggles());
