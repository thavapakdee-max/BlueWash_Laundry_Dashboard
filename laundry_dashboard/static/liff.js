(() => {
  const root = document.querySelector('.liff-shell');
  const liffId = root?.dataset.liffId || '';
  const notice = document.getElementById('notice');
  const form = document.getElementById('order-form');
  const reviewModal = document.getElementById('review-modal');
  const successModal = document.getElementById('success-modal');
  const reviewButton = document.getElementById('review-button');
  const submitButton = document.getElementById('submit-button');
  const photoInput = document.getElementById('photo');
  const photoPreview = document.getElementById('photo-preview');
  const state = { services: {}, detergents: [], softeners: [], catalogReady: false, liffReady: false };

  function message(text, type = '') {
    notice.textContent = text;
    notice.className = `notice ${type}`.trim();
  }

  function radioCard(group, value, label, description = '', icon = '✦') {
    const card = document.createElement('label');
    card.className = 'service-card';
    card.innerHTML = `<input type="radio" name="${group}" value="${escapeAttr(value)}"><span class="service-icon" aria-hidden="true">${icon}</span><span class="service-text"><b></b><small></small></span><span class="radio-dot" aria-hidden="true"></span>`;
    card.querySelector('.service-text b').textContent = label;
    card.querySelector('.service-text small').textContent = description;
    card.querySelector('input').addEventListener('change', () => {
      document.querySelectorAll(`input[name="${group}"]`).forEach(input => input.closest('label').classList.toggle('selected', input.checked));
      validateForm();
    });
    return card;
  }

  function choice(group, value, label) {
    const item = document.createElement('label');
    item.className = 'choice';
    item.innerHTML = `<input type="radio" name="${group}" value="${escapeAttr(value)}"><span class="choice-indicator" aria-hidden="true"></span><span></span>`;
    item.querySelector('span:last-child').textContent = label;
    item.querySelector('input').addEventListener('change', () => {
      document.querySelectorAll(`input[name="${group}"]`).forEach(input => input.closest('label').classList.toggle('selected', input.checked));
      validateForm();
    });
    return item;
  }

  function escapeAttr(value) {
    return String(value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }

  function selected(name) {
    return form.querySelector(`input[name="${name}"]:checked`)?.value || '';
  }

  function validateForm() {
    // Catalog selection must remain usable even while LIFF is authenticating.
    const valid = Boolean(state.catalogReady && selected('service') && selected('detergent') && selected('softener') && photoInput.files?.length);
    reviewButton.disabled = !valid;
    return valid;
  }

  function renderProducts(data) {
    state.services = data.services || {};
    state.detergents = data.detergents || [];
    state.softeners = data.softeners || [];
    const services = document.getElementById('services');
    const detergentBox = document.getElementById('detergents');
    const softenerBox = document.getElementById('softeners');
    const icons = ['◌', '◉', '✧'];
    Object.entries(state.services).forEach(([key, label], index) => services.append(radioCard('service', key, label, 'พนักงานแจ้งราคาหลังชั่งผ้า', icons[index % icons.length])));
    [...state.detergents.map(name => [name, name]), ['bring', 'นำมาเอง']].forEach(([value, label]) => detergentBox.append(choice('detergent', value, label)));
    [...state.softeners.map(name => [name, name]), ['bring', 'นำมาเอง'], ['none', 'ไม่ใช้']].forEach(([value, label]) => softenerBox.append(choice('softener', value, label)));
  }

  function labelFor(name, value) {
    if (name === 'service') return state.services[value] || value;
    if (name === 'detergent') return value === 'bring' ? 'นำมาเอง' : value;
    if (name === 'softener') return ({bring: 'นำมาเอง', none: 'ไม่ใช้'})[value] || value;
    return value;
  }

  function openReview() {
    if (!validateForm()) return;
    const rows = [
      ['บริการ', labelFor('service', selected('service'))],
      ['น้ำยาซักผ้า', labelFor('detergent', selected('detergent'))],
      ['น้ำยาปรับผ้านุ่ม', labelFor('softener', selected('softener'))],
      ['รูปผ้า', photoInput.files[0].name],
    ];
    const list = document.getElementById('review-list');
    list.replaceChildren();
    rows.forEach(([term, description]) => {
      const dt = document.createElement('dt'); dt.textContent = term;
      const dd = document.createElement('dd'); dd.textContent = description;
      list.append(dt, dd);
    });
    reviewModal.hidden = false;
  }

  async function createOrder() {
    if (!state.liffReady || !window.liff?.getIDToken?.()) {
      message('ยืนยันบัญชี LINE ไม่สำเร็จ กรุณาเปิดหน้านี้จาก LINE OA อีกครั้ง และตรวจว่า LIFF เปิด scope OpenID แล้ว', 'error');
      reviewModal.hidden = true;
      window.scrollTo({top: 0, behavior: 'smooth'});
      return;
    }
    submitButton.disabled = true;
    submitButton.textContent = 'กำลังส่งออเดอร์…';
    try {
      const idToken = window.liff.getIDToken();
      if (!idToken) throw new Error('ยืนยันบัญชี LINE ไม่สำเร็จ กรุณาปิดแล้วเปิด LIFF ใหม่');
      const data = new FormData(form);
      data.append('id_token', idToken);
      const response = await fetch('/api/liff/orders', {method: 'POST', body: data});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'ส่งออเดอร์ไม่สำเร็จ');
      reviewModal.hidden = true;
      document.getElementById('success-copy').textContent = `รหัสออเดอร์ ${result.order_code}`;
      successModal.hidden = false;
    } catch (error) {
      message(error.message || 'เชื่อมต่อไม่สำเร็จ กรุณาลองอีกครั้ง', 'error');
      submitButton.disabled = false;
      submitButton.textContent = 'ยืนยันส่งออเดอร์';
      reviewModal.hidden = true;
      window.scrollTo({top: 0, behavior: 'smooth'});
    }
  }

  document.querySelectorAll('[data-close]').forEach(button => button.addEventListener('click', () => { reviewModal.hidden = true; }));
  reviewButton.addEventListener('click', openReview);
  submitButton.addEventListener('click', createOrder);
  document.getElementById('done-button').addEventListener('click', () => {
    if (window.liff?.isInClient()) window.liff.closeWindow();
    else successModal.hidden = true;
  });
  photoInput.addEventListener('change', () => {
    const file = photoInput.files?.[0];
    if (file && file.size > 8 * 1024 * 1024) {
      photoInput.value = '';
      photoPreview.hidden = true;
      message('รูปต้องมีขนาดไม่เกิน 8 MB', 'error');
      validateForm();
      return;
    }
    if (file) {
      photoPreview.src = URL.createObjectURL(file);
      photoPreview.hidden = false;
      message('แนบรูปแล้ว เลือกตรวจสอบรายการก่อนส่งได้เลย', 'ok');
    }
    validateForm();
  });

  async function loadCatalog() {
    try {
      const response = await fetch('/api/liff/products', {headers: {'Accept': 'application/json'}});
      if (!response.ok) throw new Error(`โหลดรายการบริการไม่สำเร็จ (HTTP ${response.status})`);
      const data = await response.json();
      renderProducts(data);
      state.catalogReady = true;
      validateForm();
      return true;
    } catch (error) {
      message(`${error.message || 'โหลดรายการบริการไม่สำเร็จ'} กรุณาลองโหลดหน้าใหม่`, 'error');
      return false;
    }
  }

  async function start() {
    // Fetch product choices independently so an LIFF login/configuration issue
    // does not leave the entire order form blank.
    const catalogLoaded = await loadCatalog();
    if (!liffId) {
      message(catalogLoaded ? 'โหลดรายการแล้ว แต่ยังไม่ได้ตั้งค่า LIFF ID ในระบบ' : 'ยังไม่ได้ตั้งค่า LIFF ID ในระบบ และโหลดรายการไม่สำเร็จ', 'error');
      return;
    }
    try {
      if (!window.liff?.init) throw new Error('โหลด LINE LIFF SDK ไม่สำเร็จ ตรวจสอบอินเทอร์เน็ตแล้วลองใหม่');
      await window.liff.init({liffId});
      if (!window.liff.isLoggedIn()) {
        window.liff.login({redirectUri: window.location.href});
        return;
      }
      state.liffReady = true;
      if (!window.liff.getIDToken()) {
        message('เลือกบริการและผลิตภัณฑ์ได้แล้ว แต่ก่อนส่งออเดอร์ต้องเปิด scope OpenID ใน LIFF Endpoint settings', 'warning');
      } else {
        message('เลือกบริการและผลิตภัณฑ์ที่ต้องการได้เลย', 'ok');
      }
      validateForm();
    } catch (error) {
      state.liffReady = false;
      message(`${error.message || 'เชื่อมต่อ LINE ไม่สำเร็จ'}${catalogLoaded ? ' รายการยังเลือกได้ แต่ส่งออเดอร์ต้องยืนยัน LINE ให้สำเร็จก่อน' : ''}`, 'error');
    }
  }

  start();
})();
