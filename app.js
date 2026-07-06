const $ = (id) => document.getElementById(id);
const state = { items: [], selected: null, detector: null, stream: null, scanTimer: null, baseUrl: '', authenticated: false, currentWarehouse: '', koreaWarehouse: '한국포레스쿨창고', threshold: 100, alertWebhookConfigured: false };

function fmt(n){
  const num = Number(n || 0);
  return Number.isInteger(num) ? num.toLocaleString() : num.toLocaleString(undefined,{maximumFractionDigits:2});
}
function escapeHtml(s){
  return String(s ?? '').replace(/[&<>\"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;'}[c]));
}
function toast(msg){
  const el = $('toast'); el.textContent = msg; el.classList.add('show');
  setTimeout(()=>el.classList.remove('show'), 2600);
}
async function api(url, options={}){
  const res = await fetch(url, {headers:{'Content-Type':'application/json'}, credentials:'same-origin', ...options});
  const data = await res.json().catch(()=>({ok:false,error:'응답을 읽지 못했습니다.'}));
  if(res.status === 401 || data.auth_required){ showLogin(); throw new Error(data.error || '관리자 로그인이 필요합니다.'); }
  if(!data.ok) throw new Error(data.error || '요청 실패');
  return data.data;
}
function q(params){ const sp = new URLSearchParams(params); return sp.toString() ? '?' + sp.toString() : ''; }
function currentParams(extra={}){ return {...(state.currentWarehouse ? {warehouse: state.currentWarehouse} : {}), ...extra}; }
function photoSrc(item){ return item && item.photo_url ? item.photo_url : ''; }

async function checkLogin(){
  try{
    const me = await api('/api/me');
    state.authenticated = !!me.authenticated;
    if(state.authenticated){ hideLogin(); await loadAll(); }
    else showLogin();
    if(me.default_pin_warning) toast('기본 PIN 1204 사용중입니다. Railway Variables에서 ADMIN_PIN을 바꾸세요.');
  }catch(e){ showLogin(); }
}
function showLogin(){ $('loginOverlay').classList.remove('hidden'); }
function hideLogin(){ $('loginOverlay').classList.add('hidden'); }
async function login(ev){
  ev.preventDefault();
  try{
    await api('/api/login', {method:'POST', body:JSON.stringify({pin:$('adminPin').value})});
    $('adminPin').value=''; hideLogin(); await loadAll(); toast('로그인되었습니다.');
  }catch(e){ toast(e.message); }
}
async function logout(){
  try{ await api('/api/logout', {method:'POST', body:'{}'}); }catch(e){}
  state.authenticated = false; showLogin(); toast('로그아웃되었습니다.');
}
function statusBadge(item){
  const stock = Number(item.stock_qty || 0);
  if(stock <= 0) return '<span class="status zero">재고0</span>';
  if(item.status === '보류') return '<span class="status hold">보류</span>';
  if(item.status === '단종') return '<span class="status hold">단종</span>';
  if((item.warehouse || '') === state.koreaWarehouse && stock < state.threshold) return '<span class="status low">100미만</span>';
  return '<span class="status">보관중</span>';
}
function renderPhotoPreview(item){
  const box = $('itemPhotoPreview'); if(!box) return;
  const src = photoSrc(item);
  if(src){ box.className = 'photo-preview'; box.innerHTML = `<img src="${escapeHtml(src)}" alt="대표 사진">`; }
  else{ box.className = 'photo-preview empty'; box.textContent = '대표 사진 없음'; }
}
async function uploadItemPhoto(itemId){
  const input = $('itemPhoto');
  if(!input || !input.files || !input.files[0]) return null;
  const form = new FormData(); form.append('photo', input.files[0]);
  const res = await fetch(`/api/items/${itemId}/photo`, {method:'POST', body:form, credentials:'same-origin'});
  const data = await res.json().catch(()=>({ok:false,error:'사진 업로드 응답을 읽지 못했습니다.'}));
  if(res.status === 401 || data.auth_required){ showLogin(); throw new Error(data.error || '관리자 로그인이 필요합니다.'); }
  if(!data.ok) throw new Error(data.error || '사진 업로드 실패');
  input.value = ''; return data.data;
}
function makeQrCode(){
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'; let out = 'FSW-';
  for(let i=0;i<8;i++) out += chars[Math.floor(Math.random()*chars.length)];
  return out;
}
async function loadAll(){
  await Promise.all([loadConfig(), loadMeta()]);
  await Promise.all([loadDashboard(), loadItems(), loadMovements(), loadNotifications()]);
  if(window.INITIAL_QR){ $('qrInput').value = window.INITIAL_QR; lookupQR(window.INITIAL_QR); }
}
async function loadConfig(){
  const cfg = await api('/api/config');
  state.koreaWarehouse = cfg.korea_warehouse || '한국포레스쿨창고';
  state.threshold = Number(cfg.low_stock_threshold || 100);
  state.alertWebhookConfigured = !!cfg.alert_webhook_configured;
  const phoneUrls = cfg.urls.filter(u => !u.includes('127.0.0.1') && !u.includes('localhost'));
  state.baseUrl = phoneUrls[0] || cfg.urls[0] || location.origin;
  $('serverUrls').innerHTML = cfg.urls.map(u=>`<a href="${escapeHtml(u)}" target="_blank"><code>${escapeHtml(u)}</code></a>`).join(' ') +
    `<p class="hint">QR 라벨은 위 주소 기준으로 발급됩니다. Railway 공개 주소를 쓰면 LTE/5G에서도 조회됩니다.</p>`;
  $('alertStatus').innerHTML = state.alertWebhookConfigured ? '알림 웹훅 연결됨' : '알림 웹훅 미설정';
  $('alertStatus').className = state.alertWebhookConfigured ? 'pill ok' : 'pill warn';
}
async function loadDashboard(){
  const d = await api('/api/dashboard' + q(currentParams()));
  $('statTotal').textContent = d.total;
  $('statLocation').textContent = d.location_count;
  $('statZero').textContent = d.zero;
  $('statBelow').textContent = d.below100;
  $('currentWarehouseLabel').textContent = state.currentWarehouse || '전체 창고';
  renderWarehouseTabs(d.warehouses || []);
  const board = $('locationBoard');
  if(!d.locations.length){ board.innerHTML = '<p class="hint">등록된 위치가 없습니다.</p>'; }
  else {
    board.innerHTML = d.locations.map(loc => `
      <div class="low-item">
        <div><b>${escapeHtml(loc.name)}</b><span>자재 ${fmt(loc.item_count)}종</span></div>
        <strong>${fmt(loc.qty_sum)}개</strong>
      </div>`).join('');
  }
  const low = $('lowKoreaBoard');
  if(!d.low_korea || !d.low_korea.length){ low.innerHTML = '<p class="hint">한국포레스쿨창고 100개 미만 재고가 없습니다.</p>'; }
  else {
    low.innerHTML = d.low_korea.map(item => `<div class="low-item"><div><b>${escapeHtml(item.name)}</b><span>${escapeHtml(item.location || '위치미정')} · ${escapeHtml(item.qr_code)}</span></div><strong>${fmt(item.stock_qty)}${escapeHtml(item.unit)}</strong></div>`).join('');
  }
}
function renderWarehouseTabs(warehouses){
  const names = ['전체 창고'];
  const actual = warehouses.map(w=>w.name).filter(Boolean);
  [state.koreaWarehouse, '무역창고', ...actual].forEach(w=>{ if(w && !names.includes(w)) names.push(w); });
  $('warehouseTabs').innerHTML = names.map(name=>{
    const val = name === '전체 창고' ? '' : name;
    const active = val === state.currentWarehouse;
    return `<button class="tab ${active?'active':''}" onclick="setWarehouse('${escapeHtml(val).replace(/'/g, "\\'")}')">${escapeHtml(name)}</button>`;
  }).join('');
}
async function setWarehouse(name){
  state.currentWarehouse = name || '';
  await Promise.all([loadDashboard(), loadItems(), loadMovements()]);
}
async function loadMeta(){
  const meta = await api('/api/meta');
  const catSel = $('categoryFilter'); const current = catSel.value;
  catSel.innerHTML = '<option value="">전체 분류</option>' + meta.categories.map(c=>`<option>${escapeHtml(c)}</option>`).join(''); catSel.value = current;
  const whSel = $('itemWarehouse');
  whSel.innerHTML = (meta.warehouses || ['무역창고', state.koreaWarehouse]).map(w=>`<option>${escapeHtml(w)}</option>`).join('');
  if(![...whSel.options].some(o=>o.value===state.koreaWarehouse)){ whSel.insertAdjacentHTML('afterbegin', `<option>${escapeHtml(state.koreaWarehouse)}</option>`); }
  $('categoryList').innerHTML = meta.categories.map(c=>`<option value="${escapeHtml(c)}"></option>`).join('');
  $('locationList').innerHTML = meta.locations.map(c=>`<option value="${escapeHtml(c)}"></option>`).join('');
  $('supplierList').innerHTML = meta.suppliers.map(c=>`<option value="${escapeHtml(c)}"></option>`).join('');
}
async function loadItems(){
  const params = currentParams();
  if($('keyword').value.trim()) params.keyword = $('keyword').value.trim();
  if($('categoryFilter').value) params.category = $('categoryFilter').value;
  if($('locationFilter').value.trim()) params.location = $('locationFilter').value.trim();
  if($('zeroOnly').checked) params.zero = '1';
  state.items = await api('/api/items' + q(params));
  renderItems(); renderMoveSelect();
}
function renderItems(){
  const tbody = $('itemsTable').querySelector('tbody');
  if(!state.items.length){ tbody.innerHTML = '<tr><td colspan="10">등록된 자재가 없습니다.</td></tr>'; return; }
  tbody.innerHTML = state.items.map(item=>{
    const saleBtn = (item.warehouse || '') === state.koreaWarehouse ? `<button class="btn sale mini" onclick="quickSale(${item.id})">판매</button>` : '';
    return `<tr>
      <td>${statusBadge(item)}</td>
      <td>${escapeHtml(item.warehouse || '무역창고')}</td>
      <td><div class="item-name-cell">${item.photo_url ? `<img class="item-thumb" src="${escapeHtml(item.photo_url)}" alt="">` : ''}<div><div class="item-name">${escapeHtml(item.name)}</div><div class="sub">${escapeHtml(item.supplier || '')}</div></div></div></td>
      <td class="qty"><strong>${fmt(item.stock_qty)}</strong> ${escapeHtml(item.unit)}</td>
      <td>${escapeHtml(item.location || '미정')}</td>
      <td>${escapeHtml(item.category || '미분류')}</td>
      <td>${escapeHtml(item.spec || '-')}</td>
      <td><code>${escapeHtml(item.qr_code)}</code></td>
      <td><div class="row-actions">${saleBtn}<button class="btn ghost mini" onclick="selectItem(${item.id})">선택</button><a class="btn ghost mini" target="_blank" href="/labels?ids=${item.id}">QR</a></div></td>
    </tr>`;
  }).join('');
}
function renderMoveSelect(){
  const sel = $('moveItem'); const selectedId = state.selected?.id || sel.value;
  sel.innerHTML = state.items.map(i=>`<option value="${i.id}">${escapeHtml(i.name)} · ${escapeHtml(i.warehouse || '무역창고')} · ${escapeHtml(i.location||'위치미정')} · ${fmt(i.stock_qty)}${escapeHtml(i.unit)}</option>`).join('');
  if(selectedId) sel.value = selectedId;
}
async function loadMovements(){
  const rows = await api('/api/movements?limit=80');
  const tbody = $('movementTable').querySelector('tbody');
  tbody.innerHTML = rows.map(r=>`<tr>
    <td>${escapeHtml(r.created_at)}</td><td>${escapeHtml(r.item_name)}</td><td>${escapeHtml(r.action)}</td>
    <td>${fmt(r.qty)}</td><td>${fmt(r.before_qty)} → ${fmt(r.after_qty)}</td><td>${escapeHtml(r.reason || '')}</td>
  </tr>`).join('') || '<tr><td colspan="6">기록이 없습니다.</td></tr>';
}
async function loadNotifications(){
  const rows = await api('/api/notifications?limit=20');
  const box = $('notificationBoard');
  if(!rows.length){ box.innerHTML = '<p class="hint">아직 알림 기록이 없습니다.</p>'; return; }
  box.innerHTML = rows.map(r=>`<div class="notice ${r.success ? 'ok' : 'fail'}"><b>${escapeHtml(r.item_name)}</b><span>${fmt(r.stock_qty)}개 · ${escapeHtml(r.created_at)} · ${r.success ? '전송성공' : '전송실패'}</span><small>${escapeHtml(r.error || '')}</small></div>`).join('');
}
async function lookupQR(raw){
  const qr = cleanQR(raw || $('qrInput').value);
  if(!qr){ toast('QR 코드를 입력하세요.'); return; }
  try{ const item = await api('/api/lookup?qr=' + encodeURIComponent(qr)); setSelected(item); toast('QR 자재를 찾았습니다.'); }
  catch(e){ $('scannedItem').className='selected-box empty'; $('scannedItem').textContent = e.message; toast(e.message); }
}
function cleanQR(raw){
  raw = String(raw || '').trim(); const m = raw.match(/FSW-[A-Z0-9]{6,16}/i);
  if(m) return m[0].toUpperCase(); if(raw.includes('/scan/')) return raw.split('/scan/').pop().split(/[?#]/)[0].toUpperCase(); return raw.toUpperCase();
}
function setSelected(item){
  state.selected = item; if($('moveItem')) $('moveItem').value = item.id;
  const scanUrl = `${location.origin}/scan/${encodeURIComponent(item.qr_code)}`;
  $('scannedItem').className='selected-box';
  const selectedPhoto = item.photo_url ? `<div class="scan-photo"><img src="${escapeHtml(item.photo_url)}" alt="대표 사진"></div>` : '';
  $('scannedItem').innerHTML = `<b>${escapeHtml(item.name)}</b><br>${selectedPhoto}
    <span>${escapeHtml(item.warehouse || '무역창고')} · ${escapeHtml(item.category || '미분류')} · 위치 ${escapeHtml(item.location || '미정')}</span><br>
    <span class="qty">현재 ${fmt(item.stock_qty)}${escapeHtml(item.unit)}</span><br>
    <span class="sub">규격 ${escapeHtml(item.spec || '-')} · QR ${escapeHtml(item.qr_code)}</span><br>
    <div class="row-actions scan-actions"><button class="btn sale mini" onclick="quickSale(${item.id})">판매 차감</button><a class="btn ghost mini" target="_blank" href="/labels?ids=${item.id}">QR 라벨 출력</a><a class="btn ghost mini" target="_blank" href="${escapeHtml(scanUrl)}">조회화면 열기</a></div>`;
  fillItemForm(item);
}
function selectItem(id){ const item = state.items.find(x => Number(x.id) === Number(id)); if(item) setSelected(item); window.scrollTo({top:0, behavior:'smooth'}); }
function clearItemForm(){
  $('formTitle').textContent = '자재 등록';
  ['itemId','itemName','itemCategory','itemLocation','itemSpec','itemSupplier','itemQr','itemMemo'].forEach(id=>$(id).value='');
  $('itemWarehouse').value = state.currentWarehouse || state.koreaWarehouse || '무역창고';
  $('itemStock').value = 0; $('itemUnit').value='개'; $('itemPack').value=1; $('itemStatus').value='사용중'; $('itemLowAlert').checked = true;
  if($('itemPhoto')) $('itemPhoto').value = ''; renderPhotoPreview(null); state.selected = null;
}
function fillItemForm(item){
  $('formTitle').textContent = '자재 수정'; $('itemId').value = item.id; $('itemWarehouse').value = item.warehouse || '무역창고';
  $('itemName').value = item.name || ''; $('itemCategory').value = item.category || ''; $('itemLocation').value = item.location || ''; $('itemSpec').value = item.spec || ''; $('itemStock').value = item.stock_qty || 0;
  $('itemUnit').value = item.unit || '개'; $('itemPack').value = item.pack_qty || 1; $('itemSupplier').value = item.supplier || ''; $('itemQr').value = item.qr_code || ''; $('itemStatus').value = item.status || '사용중'; $('itemMemo').value = item.memo || '';
  $('itemLowAlert').checked = Number(item.low_alert_enabled ?? 1) === 1; renderPhotoPreview(item);
}
async function saveItem(ev){
  ev.preventDefault();
  const payload = { id:$('itemId').value, warehouse:$('itemWarehouse').value, name:$('itemName').value, category:$('itemCategory').value, location:$('itemLocation').value, spec:$('itemSpec').value, stock_qty:$('itemStock').value, unit:$('itemUnit').value, pack_qty:$('itemPack').value, supplier:$('itemSupplier').value, qr_code:$('itemQr').value, status:$('itemStatus').value, memo:$('itemMemo').value, low_alert_enabled:$('itemLowAlert').checked ? 1 : 0 };
  try{
    let item = await api('/api/items', {method:'POST', body:JSON.stringify(payload)});
    if($('itemPhoto') && $('itemPhoto').files && $('itemPhoto').files[0]){ item = await uploadItemPhoto(item.id); toast('저장하고 대표 사진도 등록했습니다.'); }
    else toast('저장했습니다. QR이 발급되었습니다.');
    await refresh(); const fresh = state.items.find(x => Number(x.id) === Number(item.id)) || item; setSelected(fresh);
  }catch(e){ toast(e.message); }
}
async function applyMove(ev){
  ev.preventDefault();
  const payload = { item_id:$('moveItem').value, action:$('moveAction').value, qty:$('moveQty').value, worker:$('moveWorker').value, reason:$('moveReason').value, ref_no:$('moveRef').value, memo:$('moveMemo').value };
  try{
    const res = await api('/api/movement', {method:'POST', body:JSON.stringify(payload)});
    $('moveQty').value=''; $('moveReason').value=''; $('moveRef').value=''; $('moveMemo').value='';
    await refresh(); setSelected(res.item); toast(res.alert ? `반영 완료 · 알림 ${res.alert.sent ? '전송' : '기록'}` : `반영 완료: ${res.movement.before_qty} → ${res.movement.after_qty}`);
    await loadNotifications();
  }catch(e){ toast(e.message); }
}
async function quickSale(id){
  const item = state.items.find(x=>Number(x.id)===Number(id)) || state.selected;
  if(!item){ toast('판매 처리할 자재를 선택하세요.'); return; }
  const qty = prompt(`${item.name}\n판매 수량을 입력하세요. 현재 ${fmt(item.stock_qty)}${item.unit}`);
  if(qty === null || qty === '' || Number(qty) <= 0){ return; }
  try{
    const res = await api('/api/movement', {method:'POST', body:JSON.stringify({item_id:item.id, action:'SALE', qty, reason:'인터넷판매', worker:'', memo:'판매 버튼으로 차감'})});
    await refresh(); setSelected(res.item); await loadNotifications();
    toast(res.alert ? `판매 차감 완료 · 100미만 알림 ${res.alert.sent ? '전송' : '기록'}` : '판매 수량을 재고에서 차감했습니다.');
  }catch(e){ toast(e.message); }
}
async function hideItem(){ const id = $('itemId').value; if(!id){ toast('숨김 처리할 자재를 선택하세요.'); return; } if(!confirm('이 자재를 재고판에서 숨김 처리할까요? 기록은 남아있습니다.')) return; try{ await api(`/api/items/${id}/hide`, {method:'POST', body:'{}'}); clearItemForm(); await refresh(); toast('숨김 처리했습니다.'); }catch(e){ toast(e.message); } }
async function deleteItem(){
  const id = $('itemId').value; const name = $('itemName').value || '선택 자재'; if(!id){ toast('삭제할 자재를 선택하세요.'); return; }
  const msg = `정말 "${name}" 자재를 완전히 삭제할까요?\n\n삭제하면 재고판, QR 조회, 입출고 기록, 대표 사진이 함께 삭제됩니다.\n삭제 직전 DB 백업은 자동으로 생성됩니다.`;
  if(!confirm(msg)) return; const confirmText = prompt('실수 삭제 방지를 위해 삭제할 자재명을 그대로 입력하세요.'); if(confirmText !== name){ toast('자재명이 일치하지 않아 삭제를 취소했습니다.'); return; }
  try{ await api(`/api/items/${id}/delete`, {method:'POST', body:'{}'}); clearItemForm(); await refresh(); toast('완전히 삭제했습니다.'); }catch(e){ toast(e.message); }
}
async function manualBackup(){ try{ await api('/api/backup'); toast('DB 백업을 만들었습니다.'); }catch(e){ toast(e.message); } }
async function testAlert(){ try{ const r = await api('/api/test-alert', {method:'POST', body:'{}'}); toast(r.configured ? (r.sent ? '테스트 알림을 보냈습니다.' : '테스트 알림 전송 실패: '+r.error) : '웹훅 URL이 아직 설정되지 않았습니다.'); await loadNotifications(); }catch(e){ toast(e.message); } }
async function refresh(){ await Promise.all([loadDashboard(), loadMeta(), loadItems(), loadMovements()]); }
async function startCamera(){
  try{
    if(!('BarcodeDetector' in window)) throw new Error('이 브라우저는 카메라 QR 인식을 지원하지 않습니다. USB QR 스캐너나 QR 직접입력을 사용하세요.');
    state.detector = new BarcodeDetector({formats:['qr_code']}); state.stream = await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});
    const video = $('scanVideo'); video.srcObject = state.stream; video.style.display='block'; await video.play(); $('scanStatus').textContent = '스캔중';
    state.scanTimer = setInterval(async()=>{ try{ const codes = await state.detector.detect(video); if(codes.length){ const val = codes[0].rawValue; $('qrInput').value = cleanQR(val); stopCamera(); lookupQR(val); } }catch(err){} }, 500);
  }catch(e){ toast(e.message); $('scanStatus').textContent='카메라 불가'; }
}
function stopCamera(){ if(state.scanTimer) clearInterval(state.scanTimer); state.scanTimer = null; if(state.stream){ state.stream.getTracks().forEach(t=>t.stop()); state.stream=null; } $('scanVideo').style.display='none'; $('scanStatus').textContent='대기중'; }
function bind(){
  $('lookupBtn').addEventListener('click', ()=>lookupQR()); $('qrInput').addEventListener('keydown', e=>{ if(e.key==='Enter') lookupQR(); });
  $('cameraBtn').addEventListener('click', startCamera); $('stopCameraBtn').addEventListener('click', stopCamera);
  $('loginForm').addEventListener('submit', login); $('logoutBtn').addEventListener('click', logout);
  $('itemForm').addEventListener('submit', saveItem); $('movementForm').addEventListener('submit', applyMove);
  $('newItemBtn').addEventListener('click', clearItemForm); $('hideItemBtn').addEventListener('click', hideItem); $('deleteItemBtn').addEventListener('click', deleteItem); $('backupBtn').addEventListener('click', manualBackup); $('testAlertBtn').addEventListener('click', testAlert);
  $('generateQrBtn').addEventListener('click', ()=>{ $('itemQr').value = makeQrCode(); toast('새 QR 코드를 발급했습니다. 저장을 누르면 적용됩니다.'); });
  if($('itemPhoto')) $('itemPhoto').addEventListener('change', ()=>{ const file = $('itemPhoto').files && $('itemPhoto').files[0]; if(file){ const url = URL.createObjectURL(file); $('itemPhotoPreview').className = 'photo-preview'; $('itemPhotoPreview').innerHTML = `<img src="${url}" alt="미리보기">`; } });
  ['keyword','categoryFilter','locationFilter','zeroOnly'].forEach(id=>$(id).addEventListener('input', ()=>loadItems()));
  $('moveItem').addEventListener('change', ()=>{ const item = state.items.find(i=>String(i.id)===$('moveItem').value); if(item) setSelected(item); });
}
window.selectItem = selectItem; window.quickSale = quickSale; window.setWarehouse = setWarehouse;
bind(); checkLogin();
