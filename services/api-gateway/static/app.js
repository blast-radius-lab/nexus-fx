const API = '';
let token = null;
let userId = null;
let username = null;
let ws = null;
let currentSide = 'BUY';
let isRegisterMode = false;
let refreshInterval = null;

// --- Auth ---

function toggleAuthMode() {
    isRegisterMode = !isRegisterMode;
    document.getElementById('email-group').style.display = isRegisterMode ? 'block' : 'none';
    document.getElementById('login-btn').textContent = isRegisterMode ? 'Register' : 'Sign In';
    document.getElementById('login-subtitle').textContent = isRegisterMode
        ? 'Create a new trading account'
        : 'Sign in to your trading account';
    document.getElementById('toggle-text').textContent = isRegisterMode
        ? 'Already have an account?'
        : "Don't have an account?";
    document.getElementById('toggle-link').textContent = isRegisterMode ? 'Sign In' : 'Register';
    document.getElementById('login-error').style.display = 'none';
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const user = document.getElementById('login-username').value;
    const pass = document.getElementById('login-password').value;
    const email = document.getElementById('login-email').value;
    const errorEl = document.getElementById('login-error');

    try {
        const endpoint = isRegisterMode ? '/api/auth/register' : '/api/auth/login';
        const body = isRegisterMode
            ? { username: user, password: pass, email }
            : { username: user, password: pass };

        const resp = await fetch(API + endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });

        const data = await resp.json();
        if (!resp.ok) {
            errorEl.textContent = data.detail || 'Authentication failed';
            errorEl.style.display = 'block';
            return;
        }

        token = data.token;
        userId = data.user_id;
        username = data.username;
        localStorage.setItem('nexus_token', token);
        localStorage.setItem('nexus_user_id', userId);
        localStorage.setItem('nexus_username', username);
        showDashboard();
    } catch (err) {
        errorEl.textContent = 'Connection failed. Is the server running?';
        errorEl.style.display = 'block';
    }
});

function logout() {
    token = null;
    userId = null;
    username = null;
    localStorage.removeItem('nexus_token');
    localStorage.removeItem('nexus_user_id');
    localStorage.removeItem('nexus_username');
    if (ws) { ws.close(); ws = null; }
    if (refreshInterval) { clearInterval(refreshInterval); refreshInterval = null; }
    document.getElementById('login-screen').style.display = 'flex';
    document.getElementById('dashboard').style.display = 'none';
}

// --- Dashboard ---

function showDashboard() {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('dashboard').style.display = 'block';
    document.getElementById('username-display').textContent = username;
    loadInstruments();
    connectWebSocket();
    loadAccount();
    loadOrders();
    loadTrades();
    loadHistory();
    refreshInterval = setInterval(() => {
        loadAccount();
        loadOrders();
        loadTrades();
        loadHistory();
    }, 5000);
}

async function apiFetch(path, options = {}) {
    const resp = await fetch(API + path, {
        ...options,
        headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
            ...(options.headers || {}),
        },
    });
    if (resp.status === 401) { logout(); return null; }
    return resp.json();
}

// --- Instruments ---

async function loadInstruments() {
    const data = await apiFetch('/api/prices/instruments');
    if (!data) return;
    const select = document.getElementById('order-instrument');
    select.innerHTML = '';
    data.instruments.forEach(inst => {
        const opt = document.createElement('option');
        opt.value = inst.symbol;
        opt.textContent = inst.display_name;
        select.appendChild(opt);
    });
    select.addEventListener('change', updateOrderButton);
    updateOrderButton();
}

// --- WebSocket ---

function connectWebSocket() {
    if (ws) ws.close();
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${protocol}//${location.host}/ws/prices?token=${token}`);

    ws.onopen = () => {
        document.getElementById('ws-status').classList.add('connected');
        document.getElementById('ws-label').textContent = 'Live';
    };

    ws.onmessage = (event) => {
        const data = JSON.parse(event.data);
        updatePriceGrid(data.prices || {});
    };

    ws.onclose = () => {
        document.getElementById('ws-status').classList.remove('connected');
        document.getElementById('ws-label').textContent = 'Disconnected';
        setTimeout(() => { if (token) connectWebSocket(); }, 3000);
    };

    ws.onerror = () => { ws.close(); };
}

// --- Prices ---

function updatePriceGrid(prices) {
    const grid = document.getElementById('price-grid');
    const instruments = Object.keys(prices).sort();

    if (grid.children.length !== instruments.length) {
        grid.innerHTML = '';
        instruments.forEach(inst => {
            const card = document.createElement('div');
            card.className = 'price-card';
            card.id = `price-${inst}`;
            card.innerHTML = `
                <div class="instrument">${inst.replace('_', '/')}</div>
                <div class="bid-ask">
                    <div class="bid" onclick="quickOrder('${inst}','SELL')" title="Sell">
                        <div style="font-size:10px;margin-bottom:2px">SELL</div>
                        <span id="bid-${inst}">—</span>
                    </div>
                    <div class="ask" onclick="quickOrder('${inst}','BUY')" title="Buy">
                        <div style="font-size:10px;margin-bottom:2px">BUY</div>
                        <span id="ask-${inst}">—</span>
                    </div>
                </div>
                <div class="spread" id="spread-${inst}">—</div>
            `;
            grid.appendChild(card);
        });
    }

    instruments.forEach(inst => {
        const p = prices[inst];
        const bidEl = document.getElementById(`bid-${inst}`);
        const askEl = document.getElementById(`ask-${inst}`);
        const spreadEl = document.getElementById(`spread-${inst}`);
        if (bidEl) bidEl.textContent = formatPrice(p.bid, inst);
        if (askEl) askEl.textContent = formatPrice(p.ask, inst);
        if (spreadEl) {
            const pips = inst.includes('JPY') ? (p.spread * 100).toFixed(1) : (p.spread * 10000).toFixed(1);
            spreadEl.textContent = `spread: ${pips} pips`;
        }
    });
}

function formatPrice(price, inst) {
    const decimals = inst.includes('JPY') ? 3 : 5;
    return price.toFixed(decimals);
}

// --- Orders ---

function quickOrder(instrument, side) {
    document.getElementById('order-instrument').value = instrument;
    setSide(side);
    updateOrderButton();
}

function setSide(side) {
    currentSide = side;
    document.getElementById('side-buy').className = side === 'BUY' ? 'active-buy' : '';
    document.getElementById('side-sell').className = side === 'SELL' ? 'active-sell' : '';
    updateOrderButton();
}

function onOrderTypeChange() {
    const type = document.getElementById('order-type').value;
    document.getElementById('limit-price-group').style.display = type === 'LIMIT' ? 'block' : 'none';
}

function updateOrderButton() {
    const inst = document.getElementById('order-instrument').value || 'EUR_USD';
    const btn = document.getElementById('submit-order-btn');
    btn.textContent = `${currentSide} ${inst.replace('_', '/')}`;
    btn.className = `submit-order-btn ${currentSide.toLowerCase()}`;
}

async function submitOrder() {
    const instrument = document.getElementById('order-instrument').value;
    const orderType = document.getElementById('order-type').value;
    const quantity = parseFloat(document.getElementById('order-quantity').value);
    const limitPrice = orderType === 'LIMIT'
        ? parseFloat(document.getElementById('order-limit-price').value)
        : null;

    const errorEl = document.getElementById('order-error');
    errorEl.style.display = 'none';

    if (!quantity || quantity <= 0) {
        errorEl.textContent = 'Invalid quantity';
        errorEl.style.display = 'block';
        return;
    }

    if (orderType === 'LIMIT' && (!limitPrice || limitPrice <= 0)) {
        errorEl.textContent = 'Invalid limit price';
        errorEl.style.display = 'block';
        return;
    }

    const body = {
        instrument,
        side: currentSide,
        order_type: orderType,
        quantity,
        limit_price: limitPrice,
    };

    const data = await apiFetch('/api/orders', { method: 'POST', body: JSON.stringify(body) });
    if (data && data.order_id) {
        showOrderConfirmation(instrument, currentSide, quantity, orderType);
        loadOrders();
        loadTrades();
        loadAccount();
        if (orderType === 'MARKET') {
            switchTab('trades');
        }
        setTimeout(() => { loadOrders(); loadTrades(); loadHistory(); }, 1500);
    } else if (data) {
        errorEl.textContent = data.reason || data.error || 'Order failed';
        errorEl.style.display = 'block';
    }
}

async function cancelOrder(orderId) {
    await apiFetch(`/api/orders/${orderId}`, { method: 'DELETE' });
    loadOrders();
}

// --- Data Loading ---

async function loadAccount() {
    const data = await apiFetch('/api/account/summary');
    if (!data) return;
    document.getElementById('stat-balance').textContent = `$${parseFloat(data.balance || 0).toLocaleString('en-US', { minimumFractionDigits: 2 })}`;
    document.getElementById('stat-trades').textContent = data.open_trades || 0;
}

async function loadOrders() {
    const data = await apiFetch('/api/orders?status=PENDING');
    if (!data) return;
    const orders = data.orders || [];
    const tbody = document.getElementById('orders-body');
    const empty = document.getElementById('orders-empty');

    document.getElementById('stat-pending').textContent = orders.length;

    if (orders.length === 0) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    tbody.innerHTML = orders.map(o => `
        <tr>
            <td>${formatTime(o.created_at)}</td>
            <td>${o.instrument.replace('_', '/')}</td>
            <td class="${o.side === 'BUY' ? 'positive' : 'negative'}">${o.side}</td>
            <td>${o.order_type}</td>
            <td>${o.quantity}</td>
            <td>${o.limit_price ? formatPrice(o.limit_price, o.instrument) : '—'}</td>
            <td><span class="status-badge status-${o.status}">${o.status}</span></td>
            <td><button class="btn btn-cancel" onclick="cancelOrder('${o.id}')">Cancel</button></td>
        </tr>
    `).join('');
}

async function loadTrades() {
    const data = await apiFetch('/api/trades/open');
    if (!data) return;
    const trades = data.trades || [];
    const tbody = document.getElementById('trades-body');
    const empty = document.getElementById('trades-empty');

    if (trades.length === 0) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    tbody.innerHTML = trades.map(t => `
        <tr>
            <td>${formatTime(t.filled_at || t.created_at)}</td>
            <td>${t.instrument.replace('_', '/')}</td>
            <td class="${t.side === 'BUY' ? 'positive' : 'negative'}">${t.side}</td>
            <td>${t.quantity}</td>
            <td>${t.fill_price ? formatPrice(t.fill_price, t.instrument) : '—'}</td>
            <td>${t.lp_order ? t.lp_order.lp_order_id || '—' : '—'}</td>
            <td><span class="status-badge status-${t.status}">${t.status}</span></td>
        </tr>
    `).join('');
}

async function loadHistory() {
    const data = await apiFetch('/api/trades/closed');
    if (!data) return;
    const trades = data.trades || [];
    const tbody = document.getElementById('history-body');
    const empty = document.getElementById('history-empty');

    if (trades.length === 0) {
        tbody.innerHTML = '';
        empty.style.display = 'block';
        return;
    }
    empty.style.display = 'none';

    tbody.innerHTML = trades.map(t => `
        <tr>
            <td>${formatTime(t.created_at)}</td>
            <td>${t.instrument.replace('_', '/')}</td>
            <td class="${t.side === 'BUY' ? 'positive' : 'negative'}">${t.side}</td>
            <td>${t.order_type}</td>
            <td>${t.quantity}</td>
            <td>${t.fill_price ? formatPrice(t.fill_price, t.instrument) : '—'}</td>
            <td><span class="status-badge status-${t.status}">${t.status}</span></td>
        </tr>
    `).join('');
}

// --- Tabs ---

function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add('active');
    document.getElementById('tab-orders').style.display = tabName === 'orders' ? 'block' : 'none';
    document.getElementById('tab-trades').style.display = tabName === 'trades' ? 'block' : 'none';
    document.getElementById('tab-history').style.display = tabName === 'history' ? 'block' : 'none';
}

// --- Utils ---

function formatTime(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

// --- Toast ---

function showOrderConfirmation(instrument, side, quantity, orderType) {
    const existing = document.getElementById('order-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'order-toast';
    toast.className = `order-toast ${side.toLowerCase()}`;
    toast.textContent = `${orderType} ${side} ${quantity} ${instrument.replace('_', '/')} submitted`;
    document.body.appendChild(toast);
    setTimeout(() => toast.classList.add('visible'), 10);
    setTimeout(() => {
        toast.classList.remove('visible');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// --- Init ---

(function init() {
    const savedToken = localStorage.getItem('nexus_token');
    const savedUserId = localStorage.getItem('nexus_user_id');
    const savedUsername = localStorage.getItem('nexus_username');
    if (savedToken && savedUserId) {
        token = savedToken;
        userId = savedUserId;
        username = savedUsername;
        showDashboard();
    }
})();
