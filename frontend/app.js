/**
 * 智能客服系统前端应用
 * 支持SSE流式输出、多会话、工具调用展示
 */

// 全局状态
const state = {
    currentSessionId: null,
    sessions: new Map(),
    isStreaming: false,
    stats: {
        total: 0,
        success_rate: 0,
        avg_time: 0
    }
};

// API配置
const API_BASE = window.location.origin;
const API_ENDPOINTS = {
    chat: `${API_BASE}/api/chat`,
    tickets: `${API_BASE}/api/tickets`,
    stats: `${API_BASE}/api/stats`,
    health: `${API_BASE}/api/health`
};

// DOM元素
const elements = {
    // 左侧边栏
    leftSidebar: document.getElementById('leftSidebar'),
    toggleLeft: document.getElementById('toggleLeft'),
    toggleLeftSidebar: document.getElementById('toggleLeftSidebar'),
    ticketList: document.getElementById('ticketList'),
    btnNewChat: document.getElementById('btnNewChat'),
    totalTickets: document.getElementById('totalTickets'),
    successRate: document.getElementById('successRate'),
    avgTime: document.getElementById('avgTime'),
    escalationCount: document.getElementById('escalationCount'),

    // 链路追踪
    btnTraces: document.getElementById('btnTraces'),
    tracesModal: document.getElementById('tracesModal'),
    closeTracesModal: document.getElementById('closeTracesModal'),
    tracesList: document.getElementById('tracesList'),
    tracesEscalatedOnly: document.getElementById('tracesEscalatedOnly'),
    btnRefreshTraces: document.getElementById('btnRefreshTraces'),

    // 中间对话区
    messagesContainer: document.getElementById('messagesContainer'),
    messageInput: document.getElementById('messageInput'),
    btnSend: document.getElementById('btnSend'),
    currentChatTitle: document.getElementById('currentChatTitle'),
    chatStatus: document.getElementById('chatStatus'),
    toggleTheme: document.getElementById('toggleTheme'),

    // 右侧边栏
    rightSidebar: document.getElementById('rightSidebar'),
    toggleRight: document.getElementById('toggleRight'),
    toggleRightSidebar: document.getElementById('toggleRightSidebar'),
    detailContent: document.getElementById('detailContent'),

    // 其他
    toastContainer: document.getElementById('toastContainer'),
    loadingOverlay: document.getElementById('loadingOverlay')
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    initializeApp();
});

/**
 * 初始化应用
 */
async function initializeApp() {
    try {
        // 绑定事件
        bindEvents();

        // 应用已保存主题
        applyTheme((() => { try { return localStorage.getItem('cs_theme') || 'dark'; } catch (e) { return 'dark'; } })());

        // 配置marked
        configureMarked();

        // 检查健康状态
        await checkHealth();

        // 加载统计数据
        await loadStats();

        // 加载历史工单
        await loadTickets();

        // 生成新会话ID
        createNewSession();

        // 隐藏加载遮罩
        hideLoading();

        // 定时刷新统计
        setInterval(loadStats, 30000);

    } catch (error) {
        console.error('初始化失败:', error);
        showToast('系统初始化失败，请刷新页面重试', 'error');
        hideLoading();
    }
}

/**
 * 配置Marked.js
 */
function configureMarked() {
    if (typeof marked !== 'undefined') {
        marked.setOptions({
            breaks: true,
            gfm: true,
            headerIds: false,
            mangle: false,
            sanitize: false
        });
    }
}

/**
 * 绑定事件
 */
function bindEvents() {
    // 侧边栏切换
    elements.toggleLeft?.addEventListener('click', () => toggleSidebar('left'));
    elements.toggleRight?.addEventListener('click', () => toggleSidebar('right'));
    elements.toggleLeftSidebar?.addEventListener('click', () => toggleSidebar('left'));
    elements.toggleRightSidebar?.addEventListener('click', () => toggleSidebar('right'));

    // 新建对话
    elements.btnNewChat?.addEventListener('click', createNewSession);

    // 链路追踪弹窗
    elements.btnTraces?.addEventListener('click', openTracesModal);
    elements.closeTracesModal?.addEventListener('click', () => elements.tracesModal?.classList.remove('open'));
    elements.btnRefreshTraces?.addEventListener('click', loadTraces);
    elements.tracesEscalatedOnly?.addEventListener('change', loadTraces);
    elements.tracesModal?.addEventListener('click', (e) => {
        if (e.target === elements.tracesModal) elements.tracesModal.classList.remove('open');
    });

    // 消息输入
    elements.messageInput?.addEventListener('input', handleInputChange);
    elements.messageInput?.addEventListener('keydown', handleInputKeydown);

    // 发送按钮
    elements.btnSend?.addEventListener('click', sendMessage);

    // 主题切换
    elements.toggleTheme?.addEventListener('click', toggleTheme);

    // 快捷按钮
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const message = e.currentTarget.dataset.message;
            if (message) {
                elements.messageInput.value = message;
                handleInputChange();
                sendMessage();
            }
        });
    });

    // 点击遮罩关闭侧边栏
    document.addEventListener('click', (e) => {
        if (window.innerWidth <= 768) {
            if (elements.leftSidebar.classList.contains('open') &&
                !elements.leftSidebar.contains(e.target) &&
                e.target !== elements.toggleLeft) {
                elements.leftSidebar.classList.remove('open');
            }
            if (elements.rightSidebar.classList.contains('open') &&
                !elements.rightSidebar.contains(e.target) &&
                e.target !== elements.toggleRight) {
                elements.rightSidebar.classList.remove('open');
            }
        }
    });
}

/**
 * 切换侧边栏
 */
function toggleSidebar(side) {
    if (side === 'left') {
        elements.leftSidebar.classList.toggle('open');
    } else if (side === 'right') {
        elements.rightSidebar.classList.toggle('open');
    }
}

/**
 * 应用主题（深 / 浅）
 */
function applyTheme(theme) {
    const isLight = theme === 'light';
    document.documentElement.setAttribute('data-theme', isLight ? 'light' : 'dark');
    if (elements.toggleTheme) {
        elements.toggleTheme.innerHTML = isLight
            ? '<i class="fas fa-sun"></i>'
            : '<i class="fas fa-moon"></i>';
    }
    try { localStorage.setItem('cs_theme', isLight ? 'light' : 'dark'); } catch (e) {}
}

/**
 * 切换主题
 */
function toggleTheme() {
    const current = document.documentElement.getAttribute('data-theme');
    applyTheme(current === 'light' ? 'dark' : 'light');
}

/**
 * 创建新会话
 */
function createNewSession() {
    const sessionId = generateSessionId();
    const session = {
        id: sessionId,
        userId: 'user_' + Date.now(),
        title: '新对话',
        messages: [],
        createdAt: new Date().toISOString(),
        currentAgent: null,
        confidence: 0,
        actions: []
    };

    state.sessions.set(sessionId, session);
    switchSession(sessionId);

    // 清空消息区
    elements.messagesContainer.innerHTML = `
        <div class="welcome-message">
            <div class="welcome-icon">
                <i class="fas fa-headset"></i>
            </div>
            <h2>欢迎使用智能客服系统</h2>
            <p>我是您的智能客服助手，可以帮您处理退货退款、订单查询、技术支持等问题。</p>
            <div class="quick-actions">
                <button class="quick-btn" data-message="如何申请退款？">
                    <i class="fas fa-undo"></i> 申请退款
                </button>
                <button class="quick-btn" data-message="查询订单状态">
                    <i class="fas fa-box"></i> 订单查询
                </button>
                <button class="quick-btn" data-message="产品使用问题">
                    <i class="fas fa-question-circle"></i> 技术支持
                </button>
            </div>
        </div>
    `;

    // 重新绑定快捷按钮
    document.querySelectorAll('.quick-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const message = e.currentTarget.dataset.message;
            if (message) {
                elements.messageInput.value = message;
                handleInputChange();
                sendMessage();
            }
        });
    });

    // 更新标题
    elements.currentChatTitle.textContent = '新对话';

    // 清空详情
    elements.detailContent.innerHTML = `
        <div class="empty-state">
            <i class="fas fa-clipboard-list"></i>
            <p>选择一个工单查看详情</p>
        </div>
    `;

    // 关闭移动端侧边栏
    elements.leftSidebar.classList.remove('open');
}

/**
 * 切换会话
 */
function switchSession(sessionId) {
    state.currentSessionId = sessionId;
    const session = state.sessions.get(sessionId);

    if (!session) return;

    // 更新标题
    elements.currentChatTitle.textContent = session.title;

    // 更新工单列表激活状态
    document.querySelectorAll('.ticket-item').forEach(item => {
        item.classList.toggle('active', item.dataset.sessionId === sessionId);
    });

    // 渲染消息
    renderMessages(session.messages);

    // 更新详情
    renderTicketDetail(session);
}

/**
 * 处理输入变化
 */
function handleInputChange() {
    const value = elements.messageInput.value.trim();
    elements.btnSend.disabled = !value || state.isStreaming;

    // 自动调整高度
    elements.messageInput.style.height = 'auto';
    elements.messageInput.style.height = Math.min(elements.messageInput.scrollHeight, 120) + 'px';
}

/**
 * 处理输入键盘事件
 */
function handleInputKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (!elements.btnSend.disabled) {
            sendMessage();
        }
    }
}

/**
 * 发送消息
 */
async function sendMessage() {
    const message = elements.messageInput.value.trim();
    if (!message || state.isStreaming) return;

    const session = state.sessions.get(state.currentSessionId);
    if (!session) return;

    // 清空输入
    elements.messageInput.value = '';
    handleInputChange();

    // 添加用户消息
    const userMessage = {
        role: 'user',
        content: message,
        timestamp: new Date().toISOString()
    };
    session.messages.push(userMessage);

    // 更新会话标题（使用第一条消息）
    if (session.messages.length === 1) {
        session.title = message.substring(0, 20) + (message.length > 20 ? '...' : '');
        elements.currentChatTitle.textContent = session.title;
        updateTicketList(session);
    }

    // 渲染消息
    renderMessages(session.messages);

    // 显示加载状态
    showTypingIndicator();

    // 设置流式状态
    state.isStreaming = true;
    elements.btnSend.disabled = true;

    try {
        // 使用SSE流式调用
        await streamChat(message, session);
    } catch (error) {
        console.error('发送消息失败:', error);
        showToast('消息发送失败，请重试', 'error');
        removeTypingIndicator();

        // 添加错误消息
        const errorMessage = {
            role: 'assistant',
            content: '抱歉，系统遇到了一些问题，请稍后重试。',
            timestamp: new Date().toISOString(),
            error: true
        };
        session.messages.push(errorMessage);
        renderMessages(session.messages);
    } finally {
        state.isStreaming = false;
        handleInputChange();
    }
}

/**
 * SSE流式聊天
 */
async function streamChat(message, session) {
    const response = await fetch(API_ENDPOINTS.chat, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            user_message: message,
            session_id: session.id,
            user_id: session.userId
        })
    });

    if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let assistantMessage = '';
    let currentAgent = null;
    let confidence = 0;
    let actions = [];
    let ticketId = null;
    let escalated = false;
    let escalatedReason = null;
    let escalatedPriority = null;
    let humanTicketId = null;

    removeTypingIndicator();

    // 创建助手消息元素
    const messageElement = createAssistantMessageElement();
    elements.messagesContainer.appendChild(messageElement);

    const bubbleElement = messageElement.querySelector('.message-bubble');

    // 处理单条 SSE 事件（供下面的循环与流结束时的残留行复用）
    const handleSseEvent = (data) => {
        switch (data.type) {
            case 'routing':
                currentAgent = data.agent;
                confidence = data.confidence;
                session.currentAgent = currentAgent;
                session.confidence = confidence;
                updateMessageAgent(messageElement, currentAgent);
                updateTicketDetail(session);
                break;

            case 'token':
                assistantMessage += data.token;
                bubbleElement.innerHTML = renderMarkdown(assistantMessage);
                scrollToBottom();
                break;

            case 'tool_call':
                const toolCallElement = createToolCallElement(data);
                bubbleElement.appendChild(toolCallElement);
                actions.push(`调用工具: ${data.tool_name}`);
                session.actions = actions;
                updateTicketDetail(session);
                scrollToBottom();
                break;

            case 'escalation':
                escalated = true;
                escalatedReason = data.reason;
                escalatedPriority = data.priority;
                humanTicketId = data.human_ticket_id;
                session.escalated = true;
                session.escalation_reason = data.reason;
                session.escalation_priority = data.priority;
                session.human_ticket_id = data.human_ticket_id;
                // 转人工卡片作为消息气泡的兄弟节点，避免被后续 token 渲染覆盖
                messageElement.appendChild(createEscalationCard(data));
                updateTicketDetail(session);
                scrollToBottom();
                break;

            case 'done':
                ticketId = data.ticket_id;
                if (data.escalated) {
                    escalated = true;
                    escalatedReason = data.escalated_reason || escalatedReason;
                    session.escalated = true;
                    session.escalation_reason = escalatedReason;
                }
                break;

            case 'error':
                // 直接在当前气泡显示错误信息，不抛出异常
                assistantMessage = assistantMessage || '';
                if (!assistantMessage.trim()) {
                    assistantMessage = '抱歉，我暂时无法处理您的问题，请稍后重试或转接人工客服。';
                } else {
                    assistantMessage += '\n\n⚠️ 系统处理异常，请稍后重试。';
                }
                bubbleElement.innerHTML = renderMarkdown(assistantMessage);
                scrollToBottom();
                break;
        }
    };

    // 按行缓冲解析 SSE：网络分包可能把一个 data 行拆成两半，
    // 必须跨 read 拼接完整行后再解析，否则会丢事件。
    let sseBuffer = '';
    const processSseLine = (rawLine) => {
        const line = rawLine.trim();
        if (!line.startsWith('data: ')) return;
        try {
            handleSseEvent(JSON.parse(line.slice(6)));
        } catch (e) {
            // JSON 解析失败等非关键错误静默忽略；其他异常向上抛给外层 catch
            if (e && e.message && !e.message.includes('JSON')) throw e;
        }
    };

    while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        sseBuffer += decoder.decode(value, { stream: true });
        const parts = sseBuffer.split('\n');
        sseBuffer = parts.pop() || '';   // 保留最后不完整的一行，等下次拼接
        for (const line of parts) {
            processSseLine(line);
        }
    }
    // 流结束时若仍有残留的半行，尝试作为最后一条事件处理
    if (sseBuffer.trim()) {
        processSseLine(sseBuffer);
    }

    // 添加助手消息到会话
    const assistantMsg = {
        role: 'assistant',
        content: assistantMessage,
        timestamp: new Date().toISOString(),
        agent: currentAgent,
        confidence: confidence,
        actions: actions,
        escalated: escalated,
        escalated_reason: escalatedReason,
        escalated_priority: escalatedPriority,
        human_ticket_id: humanTicketId
    };
    session.messages.push(assistantMsg);

    // 更新工单列表
    updateTicketList(session);

    // 刷新统计
    loadStats();
}

/**
 * 创建助手消息元素
 */
function createAssistantMessageElement() {
    const div = document.createElement('div');
    div.className = 'message assistant';
    div.innerHTML = `
        <div class="message-avatar">
            <i class="fas fa-robot"></i>
        </div>
        <div class="message-content">
            <div class="message-agent" style="display: none;">
                <i class="fas fa-user-tag"></i>
                <span></span>
            </div>
            <div class="message-bubble"></div>
            <div class="message-time">${formatTime(new Date())}</div>
        </div>
    `;
    return div;
}

/**
 * 更新消息Agent标签
 */
function updateMessageAgent(messageElement, agent) {
    const agentLabel = messageElement.querySelector('.message-agent');
    const agentText = messageElement.querySelector('.message-agent span');
    const avatar = messageElement.querySelector('.message-avatar');

    const agentNames = {
        'refund': '退货退款专员',
        'tech_support': '技术支持专家',
        'order_query': '订单查询专员',
        'general': '通用客服'
    };

    agentLabel.className = `message-agent agent-${agent}`;
    agentText.textContent = agentNames[agent] || agent;
    agentLabel.style.display = 'inline-flex';

    // 助手头像按 Agent 着色
    if (avatar) {
        avatar.className = `message-avatar avatar-${agent}`;
    }
}

/**
 * 创建工具调用卡片
 */
function createToolCallElement(data) {
    const card = document.createElement('div');
    card.className = 'tool-call-card';
    card.innerHTML = `
        <div class="tool-call-header">
            <div class="tool-call-title">
                <i class="fas fa-wrench"></i>
                <span>调用工具: ${data.tool_name}</span>
            </div>
            <i class="fas fa-chevron-down tool-call-toggle"></i>
        </div>
        <div class="tool-call-body">
            <div class="tool-call-section">
                <div class="tool-call-section-title">参数</div>
                <div class="tool-call-section-content">${JSON.stringify(data.params || {}, null, 2)}</div>
            </div>
            ${data.result ? `
            <div class="tool-call-section">
                <div class="tool-call-section-title">结果</div>
                <div class="tool-call-section-content">${JSON.stringify(data.result, null, 2)}</div>
            </div>
            ` : ''}
        </div>
    `;

    // 绑定展开/折叠
    card.querySelector('.tool-call-header').addEventListener('click', () => {
        card.classList.toggle('expanded');
    });

    return card;
}

/**
 * 显示打字指示器
 */
function showTypingIndicator() {
    const indicator = document.createElement('div');
    indicator.className = 'message assistant typing';
    indicator.id = 'typingIndicator';
    indicator.innerHTML = `
        <div class="message-avatar">
            <i class="fas fa-robot"></i>
        </div>
        <div class="message-content">
            <div class="message-bubble">
                <div class="typing-indicator">
                    <span></span>
                    <span></span>
                    <span></span>
                </div>
            </div>
        </div>
    `;
    elements.messagesContainer.appendChild(indicator);
    scrollToBottom();
}

/**
 * 移除打字指示器
 */
function removeTypingIndicator() {
    const indicator = document.getElementById('typingIndicator');
    if (indicator) {
        indicator.remove();
    }
}

/**
 * 渲染消息列表
 */
function renderMessages(messages) {
    // 保留欢迎消息或清空
    const welcomeMessage = elements.messagesContainer.querySelector('.welcome-message');
    if (!messages.length) {
        if (!welcomeMessage) {
            // 显示欢迎消息
            return;
        }
    } else if (welcomeMessage) {
        welcomeMessage.remove();
    }

    // 清空消息区（除了欢迎消息）
    const messagesToKeep = elements.messagesContainer.querySelector('.welcome-message');
    elements.messagesContainer.innerHTML = '';
    if (messagesToKeep) {
        elements.messagesContainer.appendChild(messagesToKeep);
    }

    // 渲染每条消息
    messages.forEach(msg => {
        const messageElement = createMessageElement(msg);
        elements.messagesContainer.appendChild(messageElement);
    });

    scrollToBottom();
}

/**
 * 创建消息元素
 */
function createMessageElement(msg) {
    const div = document.createElement('div');
    div.className = `message ${msg.role}`;

    if (msg.role === 'user') {
        div.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-user"></i>
            </div>
            <div class="message-content">
                <div class="message-bubble">${escapeHtml(msg.content)}</div>
                <div class="message-time">${formatTime(new Date(msg.timestamp))}</div>
            </div>
        `;
    } else {
        const agentClass = msg.agent ? `agent-${msg.agent}` : '';
        const agentName = msg.agent ? getAgentName(msg.agent) : '';

        div.innerHTML = `
            <div class="message-avatar">
                <i class="fas fa-robot"></i>
            </div>
            <div class="message-content">
                ${msg.agent ? `
                <div class="message-agent ${agentClass}">
                    <i class="fas fa-user-tag"></i>
                    <span>${agentName}</span>
                </div>
                ` : ''}
                <div class="message-bubble">${renderMarkdown(msg.content)}</div>
                <div class="message-time">${formatTime(new Date(msg.timestamp))}</div>
            </div>
        `;
    }

    return div;
}

/**
 * 获取Agent名称
 */
function getAgentName(agent) {
    const names = {
        'refund': '退货退款专员',
        'tech_support': '技术支持专家',
        'order_query': '订单查询专员',
        'general': '通用客服',
        'human': '人工客服',
        'router': '路由Agent'
    };
    return names[agent] || agent;
}

/**
 * 创建转人工卡片（聊天气泡内展示）
 */
function createEscalationCard(data) {
    const card = document.createElement('div');
    card.className = 'escalation-card';
    const priorityLabel = {
        'low': '低', 'normal': '普通', 'high': '高', 'urgent': '紧急'
    }[data.priority] || '普通';
    card.innerHTML = `
        <div class="escalation-header">
            <i class="fas fa-user-shield"></i>
            <span>已为您转接人工客服</span>
            <span class="escalation-priority priority-${data.priority || 'normal'}">${priorityLabel}</span>
        </div>
        <div class="escalation-body">
            <div class="escalation-row"><span class="escalation-key">原因</span><span>${escapeHtml(data.reason || '自动转人工兜底')}</span></div>
            ${data.estimated_response_time ? `<div class="escalation-row"><span class="escalation-key">预计响应</span><span>${escapeHtml(data.estimated_response_time)}</span></div>` : ''}
            ${data.human_ticket_id ? `<div class="escalation-row"><span class="escalation-key">人工工单</span><span>${escapeHtml(data.human_ticket_id)}</span></div>` : ''}
        </div>
    `;
    return card;
}

/**
 * 打开链路追踪弹窗
 */
async function openTracesModal() {
    elements.tracesModal?.classList.add('open');
    await loadTraces();
}

/**
 * 加载并渲染链路追踪
 */
async function loadTraces() {
    if (!elements.tracesList) return;
    try {
        const only = elements.tracesEscalatedOnly?.checked ? 'true' : 'false';
        const response = await fetch(`${API_BASE}/api/traces?limit=50&escalated_only=${only}`);
        const data = await response.json();
        const traces = data.traces || [];

        if (!traces.length) {
            elements.tracesList.innerHTML = `
                <div class="empty-state">
                    <i class="fas fa-stream"></i>
                    <p>暂无链路数据，发起对话后将自动记录</p>
                </div>`;
            return;
        }

        elements.tracesList.innerHTML = traces.map(t => {
            const spans = (t.spans || []).map(s =>
                `<span class="trace-span">${escapeHtml(s.name)} <b>${s.duration_ms}ms</b></span>`
            ).join('');
            const escBadge = t.escalated
                ? `<span class="trace-escalated"><i class="fas fa-user-shield"></i> 转人工</span>`
                : '';
            return `
                <div class="trace-card">
                    <div class="trace-card-head">
                        <span class="trace-msg">${escapeHtml(t.message)}</span>
                        ${escBadge}
                    </div>
                    <div class="trace-meta">
                        <span>总耗时 <b>${t.total_ms}ms</b></span>
                        <span>请求 ${escapeHtml(t.request_id.substring(0, 8))}</span>
                    </div>
                    <div class="trace-spans">${spans || '<span class="trace-span trace-span-empty">无 span</span>'}</div>
                </div>`;
        }).join('');
    } catch (error) {
        console.error('加载链路追踪失败:', error);
        elements.tracesList.innerHTML = `<div class="empty-state"><i class="fas fa-exclamation-circle"></i><p>加载失败</p></div>`;
    }
}

/**
 * 渲染Markdown
 */
function renderMarkdown(text) {
    if (typeof marked !== 'undefined') {
        return sanitizeHtml(marked.parse(text));
    }
    // 简单的换行处理（已转义，避免 XSS）
    return escapeHtml(text).replace(/\n/g, '<br>');
}

/**
 * 轻量 HTML 净化：防御 LLM / 用户内容注入脚本。
 * 优先使用 DOMPurify（若前端引入），否则用内置规则兜底。
 */
function sanitizeHtml(html) {
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html);
    }
    const div = document.createElement('div');
    div.innerHTML = html;
    div.querySelectorAll('script, iframe, object, embed, style, link, meta').forEach(el => el.remove());
    div.querySelectorAll('*').forEach(el => {
        Array.from(el.attributes).forEach(attr => {
            const name = attr.name.toLowerCase();
            const val = (attr.value || '').toLowerCase();
            if (name.startsWith('on') || val.includes('javascript:')) {
                el.removeAttribute(attr.name);
            }
        });
        if (el.tagName === 'A') {
            const href = (el.getAttribute('href') || '').toLowerCase();
            if (href.includes('javascript:')) el.removeAttribute('href');
        }
    });
    return div.innerHTML;
}

/**
 * 转义HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * 滚动到底部
 */
function scrollToBottom() {
    elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
}

/**
 * 更新工单列表
 */
function updateTicketList(session) {
    let ticketItem = document.querySelector(`.ticket-item[data-session-id="${session.id}"]`);

    if (!ticketItem) {
        // 创建新的工单项
        ticketItem = document.createElement('div');
        ticketItem.className = 'ticket-item';
        ticketItem.dataset.sessionId = session.id;
        ticketItem.addEventListener('click', () => switchSession(session.id));

        // 插入到列表顶部
        const emptyState = elements.ticketList.querySelector('.empty-state');
        if (emptyState) {
            emptyState.remove();
        }
        elements.ticketList.insertBefore(ticketItem, elements.ticketList.firstChild);
    }

    // 更新内容
    const agentClass = session.currentAgent ? `agent-${session.currentAgent}` : '';
    const agentName = session.currentAgent ? getAgentName(session.currentAgent) : '';

    ticketItem.innerHTML = `
        <div class="ticket-item-header">
            <div class="ticket-item-title">${escapeHtml(session.title)}</div>
            <div class="ticket-item-time">${formatTimeShort(new Date(session.createdAt))}</div>
        </div>
        <div class="ticket-item-preview">${escapeHtml(session.messages[session.messages.length - 1]?.content || '').substring(0, 30)}...</div>
        ${session.currentAgent ? `<span class="ticket-item-agent ${agentClass}">${agentName}</span>` : ''}
    `;

    // 更新激活状态
    document.querySelectorAll('.ticket-item').forEach(item => {
        item.classList.toggle('active', item.dataset.sessionId === state.currentSessionId);
    });
}

/**
 * 渲染工单详情
 */
function renderTicketDetail(session) {
    if (!session || !session.messages.length) {
        elements.detailContent.innerHTML = `
            <div class="empty-state">
                <i class="fas fa-clipboard-list"></i>
                <p>选择一个工单查看详情</p>
            </div>
        `;
        return;
    }

    const agentClass = session.currentAgent ? `agent-${session.currentAgent}` : '';
    const agentName = session.currentAgent ? getAgentName(session.currentAgent) : '未分配';

    const escalatedHtml = session.escalated ? `
        <div class="detail-section">
            <div class="detail-section-title">
                <i class="fas fa-user-shield"></i>
                转人工
            </div>
            <div class="escalated-banner">
                <i class="fas fa-shield-alt"></i>
                <div>
                    <div class="escalated-title">已转接人工客服</div>
                    <div class="escalated-reason">${escapeHtml(session.escalation_reason || '自动转人工兜底')}</div>
                    ${session.human_ticket_id ? `<div class="escalated-ticket">人工工单：${escapeHtml(session.human_ticket_id)}</div>` : ''}
                </div>
            </div>
        </div>
    ` : '';

    elements.detailContent.innerHTML = `
        ${escalatedHtml}
        <div class="detail-section">
            <div class="detail-section-title">
                <i class="fas fa-info-circle"></i>
                基本信息
            </div>
            <div style="font-size: 13px; color: var(--text-secondary);">
                <p><strong>工单ID:</strong> ${session.id.substring(0, 8)}...</p>
                <p><strong>创建时间:</strong> ${formatTime(new Date(session.createdAt))}</p>
                <p><strong>消息数:</strong> ${session.messages.length}</p>
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">
                <i class="fas fa-user-tag"></i>
                处理Agent
            </div>
            <div class="ticket-item-agent ${agentClass}" style="font-size: 13px; padding: 6px 12px;">
                ${agentName}
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">
                <i class="fas fa-chart-line"></i>
                置信度
            </div>
            <div class="confidence-bar">
                <div class="confidence-bar-fill" style="width: ${(session.confidence || 0) * 100}%"></div>
            </div>
            <div class="confidence-value">
                <span class="confidence-label">意图识别准确度</span>
                <span class="confidence-number">${Math.round((session.confidence || 0) * 100)}%</span>
            </div>
        </div>

        <div class="detail-section">
            <div class="detail-section-title">
                <i class="fas fa-list-check"></i>
                操作记录
            </div>
            <div class="action-list">
                ${(session.actions || []).map(action => `
                    <div class="action-item">
                        <i class="fas fa-check-circle"></i>
                        <span>${escapeHtml(action)}</span>
                        <span class="action-item-time">${formatTime(new Date())}</span>
                    </div>
                `).join('') || '<p style="color: var(--text-muted); font-size: 13px;">暂无操作记录</p>'}
            </div>
        </div>
    `;
}

/**
 * 更新工单详情
 */
function updateTicketDetail(session) {
    if (state.currentSessionId === session.id) {
        renderTicketDetail(session);
    }
}

/**
 * 加载历史工单
 */
async function loadTickets() {
    try {
        const response = await fetch(API_ENDPOINTS.tickets);
        const data = await response.json();

        if (data.tickets && data.tickets.length > 0) {
            // 清空现有列表
            elements.ticketList.innerHTML = '';

            // 渲染工单列表
            data.tickets.forEach(ticket => {
                const session = {
                    id: ticket.ticket_id,
                    userId: ticket.user_id,
                    title: ticket.user_message.substring(0, 20) + (ticket.user_message.length > 20 ? '...' : ''),
                    messages: [
                        { role: 'user', content: ticket.user_message, timestamp: ticket.timestamp },
                        { role: 'assistant', content: ticket.response, timestamp: ticket.timestamp, agent: ticket.agent_used, confidence: ticket.confidence }
                    ],
                    createdAt: ticket.timestamp,
                    currentAgent: ticket.agent_used,
                    confidence: ticket.confidence,
                    actions: ticket.actions_taken || [],
                    escalated: !!ticket.escalated,
                    escalation_reason: ticket.escalated_reason || null,
                    human_ticket_id: ticket.human_ticket_id || null
                };

                state.sessions.set(session.id, session);
                updateTicketList(session);
                if (state.currentSessionId === session.id) renderTicketDetail(session);
            });
        }
    } catch (error) {
        console.error('加载工单失败:', error);
    }
}

/**
 * 加载统计数据
 */
async function loadStats() {
    try {
        const response = await fetch(API_ENDPOINTS.stats);
        const data = await response.json();

        state.stats = data;

        // 更新UI
        elements.totalTickets.textContent = data.total || 0;
        elements.successRate.textContent = `${Math.round(data.success_rate || 0)}%`;
        elements.avgTime.textContent = `${(data.avg_time || 0).toFixed(1)}s`;
        if (elements.escalationCount) {
            elements.escalationCount.textContent = data.escalations || 0;
        }

        // 动画效果
        animateValue(elements.totalTickets, parseInt(elements.totalTickets.textContent));
        animateValue(elements.successRate, parseInt(elements.successRate.textContent), '%');

    } catch (error) {
        console.error('加载统计失败:', error);
    }
}

/**
 * 数值动画
 */
function animateValue(element, endValue, suffix = '') {
    const startValue = parseInt(element.textContent) || 0;
    const duration = 500;
    const startTime = performance.now();

    function update(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);

        const currentValue = Math.round(startValue + (endValue - startValue) * progress);
        element.textContent = currentValue + suffix;

        if (progress < 1) {
            requestAnimationFrame(update);
        }
    }

    requestAnimationFrame(update);
}

/**
 * 检查健康状态
 */
async function checkHealth() {
    try {
        const response = await fetch(API_ENDPOINTS.health);
        const data = await response.json();

        if (data.status === 'healthy') {
            elements.chatStatus.innerHTML = '<i class="fas fa-circle"></i> 在线';
            elements.chatStatus.style.color = 'var(--accent-green)';
        } else {
            elements.chatStatus.innerHTML = '<i class="fas fa-circle"></i> 异常';
            elements.chatStatus.style.color = 'var(--accent-red)';
            showToast('系统状态异常', 'warning');
        }
    } catch (error) {
        elements.chatStatus.innerHTML = '<i class="fas fa-circle"></i> 离线';
        elements.chatStatus.style.color = 'var(--accent-red)';
        showToast('无法连接到服务器', 'error');
    }
}

/**
 * 显示Toast提示
 */
function showToast(message, type = 'info') {
    const toast = document.createElement('div');
    toast.className = `toast ${type}`;

    const icons = {
        success: 'fa-check-circle',
        error: 'fa-exclamation-circle',
        warning: 'fa-exclamation-triangle',
        info: 'fa-info-circle'
    };

    toast.innerHTML = `
        <i class="fas ${icons[type]} toast-icon"></i>
        <span class="toast-message">${escapeHtml(message)}</span>
        <button class="toast-close" onclick="this.parentElement.remove()">
            <i class="fas fa-times"></i>
        </button>
    `;

    elements.toastContainer.appendChild(toast);

    // 3秒后自动关闭
    setTimeout(() => {
        toast.style.animation = 'toastSlide 0.3s ease reverse';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * 隐藏加载遮罩
 */
function hideLoading() {
    elements.loadingOverlay.classList.add('hidden');
}

/**
 * 显示加载遮罩
 */
function showLoading() {
    elements.loadingOverlay.classList.remove('hidden');
}

/**
 * 生成会话ID
 */
function generateSessionId() {
    return 'session_' + Date.now() + '_' + Math.random().toString(36).substring(2, 9);
}

/**
 * 格式化时间
 */
function formatTime(date) {
    return date.toLocaleTimeString('zh-CN', {
        hour: '2-digit',
        minute: '2-digit'
    });
}

/**
 * 格式化短时间
 */
function formatTimeShort(date) {
    const now = new Date();
    const diff = now - date;

    if (diff < 60000) return '刚刚';
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    if (diff < 604800000) return `${Math.floor(diff / 86400000)}天前`;

    return date.toLocaleDateString('zh-CN', {
        month: 'short',
        day: 'numeric'
    });
}
