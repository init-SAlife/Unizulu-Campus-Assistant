// the Programme & Module Explorer (📚 Explorer tab).
// Same structure as the second-year UniGuide explorer -- breadcrumbs, a list, then one item's
// details -- reading the FSAE handbook tables

const EX_EDITION_KEY = 'campusAI.explorerEdition';
const EX_ROWS_SHOWN = 11;            // module page: programmes shown before "Show N more"

let exEditions = null;               // [{id, year, label}] newest first
let exEditionId = null;              // chosen handbook (null = newest)
try{ const saved = parseInt(localStorage.getItem(EX_EDITION_KEY), 10); if(saved) exEditionId = saved; } catch(e){}
const exCache = new Map();           // url -> data, so back/forward is instant
let exRenderToken = 0;
let exLastAddress = '/#explore';
// what is open on the page right now (redrawn without asking the server again)
let exState = {};

function explorerLastAddress(){ return exLastAddress; }

// small helpers
function exEsc(text){
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
function exHref(page, id, params){
  let h = '#explore' + (page ? '/' + page : '') + (id ? '/' + encodeURIComponent(id) : '');
  const q = new URLSearchParams();
  Object.entries(params || {}).forEach(([k, v]) => { if(v) q.set(k, v); });
  const qs = q.toString();
  return qs ? `${h}?${qs}` : h;
}
function exParse(){
  const raw = window.location.hash.replace(/^#/, '');
  const [path, query] = raw.split('?');
  const parts = path.split('/');
  return { page: parts[1] || 'home', id: parts[2] ? decodeURIComponent(parts[2]) : null,
           params: new URLSearchParams(query || '') };
}
function exYearSem(year, sem){ return year ? `Y${year}${sem ? ' S' + sem : ''}` : ''; }
function exPlural(n, word){ return `${n} ${word}${n === 1 ? '' : 's'}`; }
function exTypeBadge(type){
  const t = String(type || '').toLowerCase();
  return `<span class="ex-type ex-type-${exEsc(t)}">${exEsc(type)}</span>`;
}
function exMeta(p, withNqf){
  const bits = [p.short_degree, p.years ? exPlural(p.years, 'year') : null,
                p.credits ? `${p.credits} credits` : null, withNqf && p.nqf ? `NQF ${p.nqf}` : null];
  return bits.filter(Boolean).join(' · ');
}
function exAskButton(question, label){
  return `<button type="button" class="ex-ask" data-ask="${exEsc(question)}">💬 ${exEsc(label || 'Ask the AI Assistant about this')}</button>`;
}

// every request carries the chosen handbook
async function exGet(path){
  const sep = path.includes('?') ? '&' : '?';
  const url = exEditionId ? `${path}${sep}edition=${exEditionId}` : path;
  if(exCache.has(url)) return exCache.get(url);
  const {res, data} = await api(url);
  if(!res.ok) throw Object.assign(new Error(data.error || 'Something went wrong.'), {status: res.status});
  // views are counted on the server: do not keep programme pages, so a later visit counts again
  if(!path.includes('/programme/')) exCache.set(url, data);
  return data;
}

async function exLoadEditions(){
  if(exEditions) return exEditions;
  try{
    const {res, data} = await api('/api/explorer/editions');
    exEditions = res.ok ? (data.editions || []) : [];
  } catch(err){ exEditions = []; }
  if(exEditionId && !exEditions.some(e => e.id === exEditionId)) exEditionId = null;
  return exEditions;
}

function exEditionPicker(current){
  if(!exEditions || exEditions.length < 2){
    return `<span class="ex-edition"><span class="ex-edition-label">Handbook:</span> <strong>${exEsc(current.year)}</strong></span>`;
  }
  const options = exEditions.map(e =>
    `<option value="${e.id}"${e.id === current.id ? ' selected' : ''}>${exEsc(e.year)}</option>`).join('');
  return `<label class="ex-edition" title="Which handbook to show">
    <span class="ex-edition-label">Handbook:</span>
    <select class="ex-edition-select" aria-label="Handbook year">${options}</select>
  </label>`;
}

function exCrumbs(parts){
  const nav = document.getElementById('exCrumbs');
  nav.innerHTML = parts.map((p, i) => {
    const last = i === parts.length - 1;
    const item = last || !p.href
      ? `<span${last ? ' aria-current="page"' : ''}>${exEsc(p.label)}</span>`
      : `<a href="${exEsc(p.href)}">${exEsc(p.label)}</a>`;
    return (i ? '<span class="ex-crumb-sep" aria-hidden="true">›</span>' : '') + item;
  }).join('');
}

// the crumb for where the student came from (?from=...)
function exFromCrumb(from){
  if(!from) return null;
  const [kind, ...rest] = from.split(':');
  const value = rest.join(':');
  if(kind === 'subject') return {label: exSubjectName(value), href: exHref('subject', value)};
  if(kind === 'search') return {label: `Search: ${value}`, href: exHref('search', value, {list: '1'})};
  if(kind === 'programme') return {label: value, href: exHref('programme', value)};
  return null;
}
const exSubjectNames = {};
function exSubjectName(slug){
  return exSubjectNames[slug] || slug.split('-').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}

function exSearchBox(placeholder, value, opts = {}){
  return `<form class="ex-search${opts.big ? ' ex-search-big' : ''}" role="search" data-search-form${opts.local ? ' data-local="1"' : ''}>
    <svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><line x1="16.5" y1="16.5" x2="21" y2="21"/></svg>
    <input type="search" name="q" value="${exEsc(value || '')}" placeholder="${exEsc(placeholder)}" aria-label="${exEsc(placeholder)}" autocomplete="off">
    ${opts.hint ? `<span class="ex-search-hint" aria-hidden="true">${exEsc(opts.hint)}</span>` : ''}
  </form>`;
}

function exProgrammeCard(p, opts = {}){
  return `<a class="ex-prog-card" href="${exHref('programme', p.code, {from: opts.from})}">
    <span class="ex-code">${exEsc(p.code)}</span>
    <span class="ex-prog-name">${exEsc(p.name)}</span>
    ${opts.department && p.department ? `<span class="ex-pill">${exEsc(p.department)}</span>` : ''}
    ${p.not_offered ? '<span class="ex-pill ex-pill-warn">Not offered</span>' : ''}
    <span class="ex-meta">${exEsc(exMeta(p, opts.nqf))}</span>
    ${opts.view ? '<span class="ex-view">View programme →</span>' : ''}
  </a>`;
}

function exSubjectTile(s){
  exSubjectNames[s.slug] = s.name;
  return `<a class="ex-subject" href="${exHref('subject', s.slug)}">
    <span class="ex-subject-icon" aria-hidden="true">${exEsc(s.icon)}</span>
    <span class="ex-subject-text"><strong>${exEsc(s.name)}</strong><small>${exPlural(s.count, 'programme')}</small></span>
  </a>`;
}

function exModuleChip(m, from){
  return `<a class="ex-mod-chip" href="${exHref('module', m.code, {from})}">
    <strong>${exEsc(m.code)}</strong><span>${exEsc(m.name || 'Not in this handbook')}</span>
  </a>`;
}

function exMessage(title, text, back = true){
  return `<div class="card ex-message">
    <h2>${exEsc(title)}</h2><p>${exEsc(text)}</p>
    ${back ? `<a class="ex-link" href="#explore">← Back to the Explorer</a>` : ''}
  </div>`;
}

// The pages
function exRenderHome(d){
  exCrumbs([{label: 'Explorer'}]);
  d.subjects.forEach(s => { exSubjectNames[s.slug] = s.name; });
  const types = (d.programme_types || []).join(' & ') || 'all';
  const tries = (d.try || []).map(t => `<a class="ex-chip" href="${exHref('search', t)}">${exEsc(t)}</a>`).join('');
  return `
  <div class="card ex-hero">
    <h1>Programme &amp; Module Explorer</h1>
    <p class="ex-sub">${exEsc(d.faculty || 'FSAE')} · ${exEsc(types)} programmes · straight from the handbook database</p>
    <div class="ex-search-row">
      ${exSearchBox('Search a module code or a programme, e.g. 4MTH222 or Computer Science', '', {big: true, hint: 'e.g. 4MTH222'})}
      ${exEditionPicker(d.edition)}
    </div>
    <div class="ex-try"><span>Try:</span>${tries}</div>
  </div>

  <div class="ex-section-head"><h2>Popular programmes</h2><span>Most viewed this week</span></div>
  <div class="ex-grid ex-grid-4">${d.popular.map(p => exProgrammeCard(p)).join('')}</div>

  <div class="ex-section-head"><h2>Browse by subject</h2><span>${d.programme_count} ${exEsc(types)} ${exEsc(d.popular[0] ? d.popular[0].short_degree : '')} programmes</span></div>
  <div class="ex-grid ex-grid-4">${d.subjects.map(exSubjectTile).join('')}</div>`;
}

function exRenderSubject(d, tab, filter){
  const s = d.subject;
  exSubjectNames[s.slug] = s.name;
  exCrumbs([{label: 'Explorer', href: '#explore'}, {label: s.name}]);
  const f = (filter || '').trim().toLowerCase();
  const match = (...texts) => !f || f.split(/\s+/).every(w => texts.join(' ').toLowerCase().includes(w));
  const from = `subject:${s.slug}`;
  const progs = d.programmes.filter(p => match(p.code, p.name, p.department));
  const mods = d.modules.filter(m => match(m.code, m.name));

  let main;
  if(tab === 'modules'){
    main = `<p class="ex-count">${exPlural(d.modules.length, 'module')} in ${exEsc(s.name)}${s.prefix ? ` (codes starting ${exEsc(s.prefix)})` : ''}</p>
    <div class="card ex-table-card"><div class="ex-table-wrap"><table class="ex-table">
      <thead><tr><th>Code</th><th>Module</th><th>Credits</th><th>When</th><th>Programmes</th></tr></thead>
      <tbody>${mods.map(m => `<tr>
        <td><a class="ex-code-link" href="${exHref('module', m.code, {from})}">${exEsc(m.code)}</a></td>
        <td>${exEsc(m.name)}</td><td>${exEsc(m.credits ?? '')}</td>
        <td>${exEsc(exYearSem(m.year, m.semester))}</td><td>${m.programme_count}</td></tr>`).join('')
        || '<tr><td colspan="5" class="ex-empty">No modules match your search.</td></tr>'}</tbody>
    </table></div></div>`;
  } else {
    main = `<p class="ex-count">${exPlural(d.programmes.length, 'programme')} include ${exEsc(s.name)}</p>
    <div class="ex-grid ex-grid-3">${progs.map(p => exProgrammeCard(p, {from, department: true, nqf: true, view: true})).join('')
      || '<p class="ex-empty">No programmes match your search.</p>'}</div>`;
  }
  const core = d.core_modules.map(m => `<a class="ex-side-mod" href="${exHref('module', m.code, {from})}">
      <strong>${exEsc(m.code)}</strong><span>${exEsc(m.name)}</span><em>${exEsc(exYearSem(m.year, m.semester))}</em></a>`).join('');
  const year = d.edition.year;
  return `
  <div class="ex-toolbar">
    ${exSearchBox(`Search within ${s.name}`, filter, {local: true})}
    <div class="ex-tabs" role="tablist">
      <button type="button" role="tab" data-subject-tab="programmes" aria-selected="${tab !== 'modules'}" class="${tab !== 'modules' ? 'active' : ''}">Programmes (${d.programmes.length})</button>
      <button type="button" role="tab" data-subject-tab="modules" aria-selected="${tab === 'modules'}" class="${tab === 'modules' ? 'active' : ''}">Modules (${d.modules.length})</button>
    </div>
    ${exEditionPicker(d.edition)}
  </div>
  <div class="ex-split">
    <div class="ex-split-main">${main}</div>
    <aside class="ex-split-side">
      ${d.modules.length ? `<div class="card ex-side-card">
        <h3>${exEsc(s.name)} modules</h3>
        <p class="ex-side-sub">Core modules taught in the ${exEsc(s.name)} major.</p>
        <div class="ex-side-list">${core}</div>
        ${tab !== 'modules' ? `<button type="button" class="ex-link" data-subject-tab="modules">See all ${exPlural(d.modules.length, 'module')} →</button>` : ''}
      </div>` : ''}
      <div class="card ex-side-card">
        <h3>Not sure which one?</h3>
        <p class="ex-side-sub">Ask the AI Assistant to compare programmes or explain the requirements.</p>
        ${exAskButton(`I'm interested in ${s.name}. Compare the ${s.name} programmes in the ${year} handbook and explain their admission requirements.`, 'Ask the AI Assistant')}
      </div>
    </aside>
  </div>`;
}

function exRequisites(m){
  const short = list => list.length > 3 ? `${list.slice(0, 3).join(', ')} +${list.length - 3}` : list.join(', ');
  const parts = [];
  if(m.prerequisites && m.prerequisites.length) parts.push(`<span title="${exEsc(m.prerequisites.join(', '))}">Pre-req: ${exEsc(short(m.prerequisites))}</span>`);
  if(m.corequisites && m.corequisites.length) parts.push(`<span title="${exEsc(m.corequisites.join(', '))}">Co-req: ${exEsc(short(m.corequisites))}</span>`);
  return parts.length ? parts.join('<br>') : '<span class="ex-dash">—</span>';
}

function exCurriculumRows(list, from){
  return list.map(m => `<tr>
    <td><a class="ex-code-link" href="${exHref('module', m.code, {from})}">${exEsc(m.code)}</a></td>
    <td>${exEsc(m.name)}</td><td>${exEsc(m.credits ?? '')}</td>
    <td>${exTypeBadge(m.type)}</td><td class="ex-req">${exRequisites(m)}</td></tr>`).join('');
}

function exRenderProgramme(d, yearIndex, fromParam){
  const crumbs = [{label: 'Explorer', href: '#explore'}];
  const from = exFromCrumb(fromParam) ||
    (d.subject_slugs && d.subject_slugs[0] ? exFromCrumb(`subject:${d.subject_slugs[0]}`) : null);
  (d.subjects || []).forEach((name, i) => { if(d.subject_slugs[i]) exSubjectNames[d.subject_slugs[i]] = name; });
  if(from) crumbs.push(from);
  crumbs.push({label: d.code});
  exCrumbs(crumbs);

  const year = d.edition.year;
  const chips = [
    d.years ? `<b>${d.years}</b> years` : null, d.credits ? `<b>${d.credits}</b> credits` : null,
    d.nqf ? `NQF <b>${d.nqf}</b>` : null, d.presentation, d.intake ? `${d.intake} intake` : null,
    `${year} handbook`,
  ].filter(Boolean).map(c => `<span class="ex-chip-static">${c.includes('<b>') ? c : exEsc(c)}</span>`).join('');

  const admission = d.admission.map(g => g.one_of
    ? `<div class="ex-adm-oneof"><span class="ex-adm-oneof-label">One of:</span>${g.items.map(i =>
        `<div class="ex-row"><span>${exEsc(i.subject)}</span><b>${i.percentage}% · level ${i.level}</b></div>`).join('')}</div>`
    : g.items.map(i => `<div class="ex-row"><span>${exEsc(i.subject)}</span><b>${i.percentage}% · level ${i.level}</b></div>`).join('')
  ).join('') || '<p class="ex-empty">No admission requirements are stored for this programme.</p>';

  const details = [
    ['Faculty', d.faculty], ['Department', d.department],
    ['Majors', (d.subjects || []).join(', ')], ['Readmission', d.readmission],
  ].filter(r => r[1]).map(r => `<div class="ex-row ex-row-top"><span>${exEsc(r[0])}</span><b>${exEsc(r[1])}</b></div>`).join('');

  const cur = d.curriculum;
  const yi = Math.min(Math.max(yearIndex || 0, 0), Math.max(cur.length - 1, 0));
  const tabs = cur.map((y, i) => `<button type="button" class="ex-year-tab${i === yi ? ' active' : ''}" data-year-index="${i}" aria-pressed="${i === yi}">Year ${y.year}</button>`).join('');
  const selfFrom = `programme:${d.code}`;
  const yearData = cur[yi];
  const semesters = yearData ? yearData.semesters.map(s => `
    <h4 class="ex-sem">Semester ${s.semester}</h4>
    <div class="ex-table-wrap"><table class="ex-table ex-curriculum">
      <thead><tr><th>Code</th><th>Module</th><th>Credits</th><th>Type</th><th>Requisites</th></tr></thead>
      <tbody>${exCurriculumRows(s.modules, selfFrom)}
      ${s.groups.map(g => `<tr class="ex-group-row"><td colspan="5"><b>Choose ${g.choose} from group ${exEsc(g.name)}</b> · elective</td></tr>
        ${exCurriculumRows(g.modules, selfFrom)}`).join('')}</tbody>
    </table></div>`).join('') : '<p class="ex-empty">No modules are stored for this programme.</p>';

  return `
  <div class="card ex-head">
    <div class="ex-head-text">
      <p class="ex-eyebrow">${exEsc(d.code)} · ${exEsc(d.degree || 'Programme')}</p>
      <h1>${exEsc(d.name)}</h1>
      ${d.not_offered ? '<p class="ex-warn">The handbook marks this programme as <b>not offered</b>.</p>' : ''}
      <div class="ex-chips">${chips}</div>
    </div>
    <div class="ex-head-ask">
      ${exAskButton(`Tell me about the ${d.code} ${d.name} programme in the ${year} handbook: the admission requirements, the majors and the modules in each year.`)}
      <small>Opens a chat about ${exEsc(d.code)}</small>
    </div>
  </div>
  <div class="ex-detail">
    <div class="ex-detail-side">
      <div class="card ex-side-card">
        <h3>Admission requirements</h3>
        ${admission}
        ${d.admission_note ? `<p class="ex-note">${exEsc(d.admission_note)}</p>` : ''}
      </div>
      <div class="card ex-side-card">
        <h3>Programme details</h3>
        ${details}
      </div>
    </div>
    <div class="card ex-detail-main">
      <div class="ex-cur-head"><h3>Curriculum</h3><div class="ex-year-tabs">${tabs}</div></div>
      ${semesters}
      <div class="ex-totals">
        ${yearData ? `<span>Year ${yearData.year} total: <b>${yearData.credits} credits</b></span>` : ''}
        ${d.credits ? `<span>Programme total: <b>${d.credits} credits</b></span>` : ''}
      </div>
      <p class="ex-source">Source: ${exEsc(d.edition.label || `${year} handbook`)}</p>
    </div>
  </div>`;
}

function exRenderModule(d, typeFilter, showAll, fromParam){
  const crumbs = [{label: 'Explorer', href: '#explore'}];
  const from = exFromCrumb(fromParam) || (d.subject ? exFromCrumb(`subject:${d.subject.slug}`) : null);
  if(d.subject) exSubjectNames[d.subject.slug] = d.subject.name;
  if(from) crumbs.push(from);
  crumbs.push({label: d.code});
  exCrumbs(crumbs);

  const year = d.edition.year;
  const chips = [
    d.credits ? `<b>${d.credits}</b> credits` : null, d.nqf ? `NQF <b>${d.nqf}</b>` : null,
    d.year ? `Year <b>${d.year}</b>${d.semester ? ` · Semester <b>${d.semester}</b>` : ''}` : null,
    `${year} handbook`,
  ].filter(Boolean).map(c => `<span class="ex-chip-static">${c}</span>`).join('');

  const selfFrom = fromParam && fromParam.startsWith('search:') ? fromParam : null;
  const chipsList = (list, empty) => list.length
    ? `<div class="ex-mod-chips">${list.map(m => exModuleChip(m, selfFrom)).join('')}</div>`
    : `<p class="ex-none">${empty}</p>`;

  const counts = d.type_counts || {};
  const filters = [['All', d.programmes.length]].concat(
    ['Compulsory', 'Major', 'Elective'].filter(t => counts[t]).map(t => [t, counts[t]]));
  const active = filters.some(f => f[0] === typeFilter) ? typeFilter : 'All';
  const rows = d.programmes.filter(p => active === 'All' || p.type === active);
  const shown = showAll ? rows : rows.slice(0, EX_ROWS_SHOWN);
  const more = rows.length - shown.length;
  const types = [...new Set(d.programmes.map(p => p.programme_type).filter(Boolean))];

  return `
  <div class="card ex-head">
    <div class="ex-head-text">
      <p class="ex-eyebrow">${exEsc(d.code)} · Module</p>
      <h1>${exEsc(d.name)}</h1>
      <div class="ex-chips">${chips}</div>
    </div>
    <div class="ex-head-ask">
      ${exAskButton(`Tell me about module ${d.code} (${d.name}) in the ${year} handbook: its prerequisites and which programmes include it.`)}
    </div>
  </div>
  <div class="ex-detail">
    <div class="ex-detail-side">
      <div class="card ex-side-card">
        <h3>Prerequisites</h3>
        ${d.prerequisites.length ? '<p class="ex-side-sub">You need to have passed:</p>' : ''}
        ${chipsList(d.prerequisites, 'None')}
        <h3 class="ex-h3-gap">Co-requisites</h3>
        ${d.corequisites.length ? '<p class="ex-side-sub">Taken together with (or after):</p>' : ''}
        ${chipsList(d.corequisites, 'None')}
      </div>
      <div class="card ex-side-card">
        <h3>Leads to</h3>
        ${d.leads_to.length ? `<p class="ex-side-sub">Modules that need ${exEsc(d.code)}:</p>` : ''}
        ${chipsList(d.leads_to, 'No module lists it as a prerequisite.')}
      </div>
    </div>
    <div class="card ex-detail-main">
      <div class="ex-cur-head"><h3>Found in ${exPlural(d.programmes.length, 'programme')}</h3>
        <span class="ex-dim">${exEsc(types.join(', ') || 'No programmes')} · ${exEsc(year)}</span></div>
      <div class="ex-filter-chips">${filters.map(([t, n]) =>
        `<button type="button" class="ex-filter${t === active ? ' active' : ''}" data-type-filter="${t}" aria-pressed="${t === active}">${t} ${n}</button>`).join('')}</div>
      <div class="ex-table-wrap"><table class="ex-table">
        <thead><tr><th>Code</th><th>Programme</th><th>In this programme</th><th>Note</th><th></th></tr></thead>
        <tbody>${shown.map(p => `<tr>
          <td><a class="ex-code-link" href="${exHref('programme', p.code, {from: p.subject_slug ? `subject:${p.subject_slug}` : null})}">${exEsc(p.code)}</a></td>
          <td>${exEsc(p.name)}${p.not_offered ? ' <span class="ex-pill ex-pill-warn">Not offered</span>' : ''}</td>
          <td>${exTypeBadge(p.type)}</td>
          <td class="ex-dim">${p.note ? exEsc(p.note) : '<span class="ex-dash">—</span>'}</td>
          <td class="ex-right"><a class="ex-link" href="${exHref('programme', p.code, {from: p.subject_slug ? `subject:${p.subject_slug}` : null})}">View →</a></td>
        </tr>`).join('') || '<tr><td colspan="5" class="ex-empty">No programme in this handbook includes this module.</td></tr>'}</tbody>
      </table></div>
      ${more > 0 ? `<button type="button" class="ex-link ex-more" data-show-all>Show ${more} more →</button>` : ''}
      <p class="ex-source">Source: ${exEsc(d.edition.label || `${year} handbook`)}</p>
    </div>
  </div>`;
}

function exRenderSearch(d){
  exCrumbs([{label: 'Explorer', href: '#explore'}, {label: `Search: ${d.query}`}]);
  const from = `search:${d.query}`;
  const nothing = !d.subjects.length && !d.programmes.length && !d.modules.length;
  return `
  <div class="card ex-hero ex-hero-slim">
    <div class="ex-search-row">
      ${exSearchBox('Search a module code or a programme, e.g. 4MTH222 or Computer Science', d.query, {big: true})}
      ${exEditionPicker(d.edition)}
    </div>
  </div>
  ${nothing ? exMessage(`Nothing found for “${d.query}”`,
      `No subject, programme or module in the ${d.edition.year} handbook matches. Try a module code such as 4MTH222, or a word such as Calculus.`) : ''}
  ${d.subjects.length ? `<div class="ex-section-head"><h2>Subjects</h2></div>
    <div class="ex-grid ex-grid-4">${d.subjects.map(exSubjectTile).join('')}</div>` : ''}
  ${d.programmes.length ? `<div class="ex-section-head"><h2>Programmes</h2><span>${d.programmes.length}</span></div>
    <div class="ex-grid ex-grid-3">${d.programmes.map(p => exProgrammeCard(p, {from, department: true, nqf: true, view: true})).join('')}</div>` : ''}
  ${d.modules.length ? `<div class="ex-section-head"><h2>Modules</h2><span>${d.module_total > d.modules.length ? `first ${d.modules.length} of ${d.module_total}` : d.modules.length}</span></div>
    <div class="card ex-table-card"><div class="ex-table-wrap"><table class="ex-table">
      <thead><tr><th>Code</th><th>Module</th><th>Credits</th><th>When</th></tr></thead>
      <tbody>${d.modules.map(m => `<tr>
        <td><a class="ex-code-link" href="${exHref('module', m.code, {from})}">${exEsc(m.code)}</a></td>
        <td>${exEsc(m.name)}</td><td>${exEsc(m.credits ?? '')}</td><td>${exEsc(exYearSem(m.year, m.semester))}</td></tr>`).join('')}</tbody>
    </table></div></div>` : ''}`;
}

// Router: draws the page named in the address bar
async function exRoute(){
  const token = ++exRenderToken;
  const {page, id, params} = exParse();
  exLastAddress = '/' + window.location.hash;
  const box = document.getElementById('exPage');
  const loading = setTimeout(() => { if(token === exRenderToken) box.innerHTML = '<div class="ex-loading">Loading from the handbook…</div>'; }, 150);
  try{
    await exLoadEditions();
    let html;
    if(page === 'subject' && id){
      const d = await exGet(`/api/explorer/subject/${encodeURIComponent(id)}`);
      exState = {page, data: d, tab: params.get('tab') === 'modules' ? 'modules' : 'programmes', filter: ''};
      html = exRenderSubject(d, exState.tab, '');
    } else if(page === 'programme' && id){
      const d = await exGet(`/api/explorer/programme/${encodeURIComponent(id)}`);
      exState = {page, data: d, year: 0, from: params.get('from')};
      html = exRenderProgramme(d, 0, exState.from);
    } else if(page === 'module' && id){
      const d = await exGet(`/api/explorer/module/${encodeURIComponent(id)}`);
      exState = {page, data: d, filter: 'All', all: false, from: params.get('from')};
      html = exRenderModule(d, 'All', false, exState.from);
    } else if(page === 'search' && id){
      const d = await exGet(`/api/explorer/search?q=${encodeURIComponent(id)}`);
      if(token !== exRenderToken) return;
      // a code opens its page straight away (unless the student came back to the list)
      if(d.exact && params.get('list') !== '1'){
        clearTimeout(loading);
        window.location.replace(exHref(d.exact.kind, d.exact.code, {from: `search:${id}`}));
        return;
      }
      exState = {page, data: d};
      html = exRenderSearch(d);
    } else {
      const d = await exGet('/api/explorer/overview');
      exState = {page: 'home', data: d};
      html = exRenderHome(d);
    }
    if(token !== exRenderToken) return;
    box.innerHTML = html;
  } catch(err){
    if(token !== exRenderToken || err.message === 'signed out') return;
    console.error(err);
    exCrumbs([{label: 'Explorer', href: '#explore'}]);
    box.innerHTML = err.status === 404
      ? exMessage('Not found', err.message)
      : exMessage('The handbook could not be loaded', err.message && err.status ? err.message
          : 'Could not reach the server. Check your connection and try again.');
  } finally {
    clearTimeout(loading);
  }
}

// redraw the current page from exState (tabs, filters) without asking the server again
function exRedraw(){
  const box = document.getElementById('exPage');
  const s = exState;
  if(s.page === 'subject') box.innerHTML = exRenderSubject(s.data, s.tab, s.filter);
  else if(s.page === 'programme') box.innerHTML = exRenderProgramme(s.data, s.year, s.from);
  else if(s.page === 'module') box.innerHTML = exRenderModule(s.data, s.filter, s.all, s.from);
}

function onExplorerShown(){ exRoute(); }

// clicks and typing inside the Explorer
const exPageBox = document.getElementById('exPage');

exPageBox.addEventListener('submit', event => {
  const form = event.target.closest('[data-search-form]');
  if(!form) return;
  event.preventDefault();
  const q = form.q.value.trim();
  if(!q || form.dataset.local) return;
  window.location.hash = exHref('search', q);
});

// "Search within <subject>": filters the cards / module rows as you type
exPageBox.addEventListener('input', event => {
  const form = event.target.closest('[data-search-form][data-local]');
  if(!form || exState.page !== 'subject') return;
  exState.filter = event.target.value;
  const pos = event.target.selectionStart;
  exRedraw();
  const input = exPageBox.querySelector('[data-search-form][data-local] input');
  if(input){ input.focus(); input.setSelectionRange(pos, pos); }
});

exPageBox.addEventListener('change', event => {
  if(!event.target.classList.contains('ex-edition-select')) return;
  exEditionId = parseInt(event.target.value, 10) || null;
  try{ localStorage.setItem(EX_EDITION_KEY, String(exEditionId)); } catch(e){}
  exRoute();
});

exPageBox.addEventListener('click', event => {
  const ask = event.target.closest('[data-ask]');
  if(ask){ askAssistant(ask.dataset.ask); return; }

  const tab = event.target.closest('[data-subject-tab]');
  if(tab && exState.page === 'subject'){
    exState.tab = tab.dataset.subjectTab;
    history.replaceState(null, '', exHref('subject', exState.data.subject.slug, {tab: exState.tab === 'modules' ? 'modules' : null}));
    exLastAddress = '/' + window.location.hash;
    exRedraw();
    return;
  }
  const yearTab = event.target.closest('[data-year-index]');
  if(yearTab && exState.page === 'programme'){
    exState.year = parseInt(yearTab.dataset.yearIndex, 10) || 0;
    exRedraw();
    return;
  }
  const filter = event.target.closest('[data-type-filter]');
  if(filter && exState.page === 'module'){
    exState.filter = filter.dataset.typeFilter;
    exState.all = false;
    exRedraw();
    return;
  }
  if(event.target.closest('[data-show-all]') && exState.page === 'module'){
    exState.all = true;
    exRedraw();
  }
});
