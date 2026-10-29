/**
 * MateEngine Bridge Extension for SillyTavern
 * Connects SillyTavern to MateEngine desktop app for avatar control.
 */

import { getContext, extension_settings } from '../../../extensions.js';
import {
    eventSource,
    event_types,
    saveSettingsDebounced,
    sendMessageAsUser,
    Generate,
    setExtensionPrompt,
    extension_prompt_types,
    extension_prompt_roles,
    addOneMessage,
    saveChatConditional,
    isGenerating,
} from '../../../../script.js';

// Try to import slash command registration - different locations in different ST versions
let registerSlashCommand;
try {
    const slashModule = await import('../../../slash-commands.js');
    registerSlashCommand = slashModule.registerSlashCommand;
} catch (e) {
    try {
        const slashModule = await import('../../../../scripts/slash-commands.js');
        registerSlashCommand = slashModule.registerSlashCommand;
    } catch (e2) {
        console.log('[MateEngine] Slash commands not available, using fallback');
        registerSlashCommand = null;
    }
}

const MODULE_NAME = 'MateEngine';
const DEBUG_PREFIX = '[MateEngine]';

/**
 * Checks whether text is an interim or typing placeholder (e.g. "...", "…", whitespace, tags)
 */
function isPlaceholder(text) {
    if (!text || typeof text !== 'string') return true;
    const stripped = text.replace(/<[^>]*>/g, '').trim();
    if (stripped === '' || /^[\.\s…]+$/.test(stripped)) return true;
    if (stripped === '...' || stripped === '…' || stripped === '[thinking]' || stripped === 'Loading...') return true;
    return false;
}

let lastDeliveredResponse = null;
let lastKnownChatBoxOpen = true;


// Default settings
const defaultSettings = {
    enabled: true,
    bridgeUrl: 'http://127.0.0.1:8787',
    autoSendExpressions: true,
    autoLipSync: true,
    debugLogging: false,
    inChatPokeEnabled: true,
    desktopBubblesEnabled: false,   // Disabled by default so speech bubbles don't stack on desktop
    autoDismissBubbles: true,       // Auto-dismiss desktop speech bubbles if enabled
    bubbleDismissTimeoutSec: 5,     // 5 seconds auto-dismissal
};

// Emotion mapping: SillyTavern classify labels -> MateEngine emotions
const EMOTION_MAP = {
    // Joy family
    'joy': 'joy',
    'amusement': 'joy',
    'excitement': 'joy',
    'approval': 'joy',
    'gratitude': 'joy',
    'optimism': 'joy',
    'pride': 'joy',
    'relief': 'joy',

    // Anger family
    'anger': 'angry',
    'annoyance': 'angry',
    'disapproval': 'angry',
    'disgust': 'angry',

    // Sadness family
    'sadness': 'sorrow',
    'grief': 'sorrow',
    'disappointment': 'sorrow',
    'remorse': 'sorrow',

    // Love/Fun family
    'love': 'fun',
    'desire': 'fun',
    'caring': 'fun',
    'admiration': 'fun',

    // Neutral
    'neutral': 'neutral',
    'confusion': 'neutral',
    'curiosity': 'neutral',
    'realization': 'neutral',
    'surprise': 'neutral',

    // Fear -> slight sorrow
    'fear': 'sorrow',
    'nervousness': 'sorrow',
    'embarrassment': 'sorrow',
};

// Action mapping: keywords -> animation names
const ACTION_MAP = {
    // Jump actions
    'jump': 'action_jump',
    'jumping': 'action_jump',
    'jumps': 'action_jump',
    'jumped': 'action_jump',
    'leap': 'action_jump',
    'leaps': 'action_jump',
    'leaping': 'action_jump',
    'leaped': 'action_jump',
    'hop': 'action_jump',
    'hops': 'action_jump',
    'hopping': 'action_jump',

    // Run actions
    'run': 'action_run',
    'running': 'action_run',
    'runs': 'action_run',
    'sprint': 'action_run',
    'sprinting': 'action_run',
    'sprints': 'action_run',
    'dash': 'action_run',
    'dashing': 'action_run',

    // Walk/Jog actions
    'walk': 'action_walk',
    'walking': 'action_walk',
    'walks': 'action_walk',
    'jog': 'action_jog',
    'jogging': 'action_jog',
    'jogs': 'action_jog',

    // Dance actions
    'dance': 'dance_1',
    'dancing': 'dance_1',
    'dances': 'dance_1',

    // Greeting actions
    'wave': 'action_greeting',
    'waving': 'action_greeting',
    'waves': 'action_greeting',
    'greet': 'action_greeting',
    'greeting': 'action_greeting',
    'greets': 'action_greeting',

    // Crouch/Sit actions
    'crouch': 'action_crouch',
    'crouching': 'action_crouch',
    'crouches': 'action_crouch',
    'sit': 'action_crouch',
    'sitting': 'action_crouch',
    'sits': 'action_crouch',

    // Lay actions
    'lay': 'action_laydown',
    'laying': 'action_laydown',
    'lie': 'action_laydown',
    'lying': 'action_laydown',
    'lays': 'action_laydown',

    // Crawl actions
    'crawl': 'action_crawling',
    'crawling': 'action_crawling',
    'crawls': 'action_crawling',

    // Stand actions
    'stand': 'action_standup',
    'standing': 'action_standup',
    'stands': 'action_standup',

    // Kneel actions
    'kneel': 'kneel_idle',
    'kneeling': 'kneel_idle',
    'kneels': 'kneel_idle',
};

/**
 * Load extension settings
 */
function loadSettings() {
    extension_settings[MODULE_NAME] = extension_settings[MODULE_NAME] || {};

    for (const key in defaultSettings) {
        if (extension_settings[MODULE_NAME][key] === undefined) {
            extension_settings[MODULE_NAME][key] = defaultSettings[key];
        }
    }
}

/**
 * Send a command to MateEngine bridge
 */
async function sendToBridge(endpoint, data) {
    if (!extension_settings[MODULE_NAME].enabled) {
        console.log(`${DEBUG_PREFIX} Bridge disabled, skipping ${endpoint}`);
        return null;
    }

    const url = `${extension_settings[MODULE_NAME].bridgeUrl}/api/${endpoint}`;
    console.log(`${DEBUG_PREFIX} Sending to bridge: ${url}`, data);

    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const result = await response.json();
        console.log(`${DEBUG_PREFIX} Bridge response:`, result);
        return result;
    } catch (error) {
        console.error(`${DEBUG_PREFIX} Bridge error for ${endpoint}:`, error.message);
        return null;
    }
}

/**
 * Check bridge connection status
 */
async function checkBridgeStatus() {
    const url = `${extension_settings[MODULE_NAME].bridgeUrl}/api/status`;

    try {
        const response = await fetch(url);
        if (response.ok) {
            return await response.json();
        }
    } catch (error) {
        return null;
    }
    return null;
}

/**
 * Set avatar expression
 */
async function setExpression(emotion, value = 1.0) {
    const mappedEmotion = EMOTION_MAP[emotion.toLowerCase()] || 'neutral';
    return sendToBridge('expression', { emotion: mappedEmotion, value });
}

/**
 * Set animation parameter
 */
async function setAnimation(param, value = true, isTrigger = false) {
    return sendToBridge('animation', { param, value, isTrigger });
}

/**
 * Start lip sync with text
 */
async function speak(text, emotion = null) {
    const data = { text };
    if (emotion) {
        data.emotion = EMOTION_MAP[emotion.toLowerCase()] || emotion;
    }
    return sendToBridge('speak', data);
}

/**
 * Stop lip sync
 */
async function stopSpeaking() {
    return sendToBridge('lipsync', { stop: true });
}

let bubbleDismissTimer = null;

/**
 * Schedule auto-dismissal of desktop bubbles in MateEngine
 */
function scheduleBubbleDismiss(delaySeconds = 5) {
    if (bubbleDismissTimer) clearTimeout(bubbleDismissTimer);
    bubbleDismissTimer = setTimeout(async () => {
        try {
            await sendToBridge('chat', { text: '', showBubble: false });
        } catch (_) {}
    }, Math.max(1000, delaySeconds * 1000));
}

/**
 * Send complete chat message to MateEngine (for expressions, lip sync, and optional bubble)
 */
async function sendChatMessage(text, characterName = null, emotion = null, showBubble = null) {
    const settings = extension_settings[MODULE_NAME] || {};
    // Determine whether to show bubble: default false for spontaneous, respect setting otherwise
    const shouldShowBubble = (showBubble !== null)
        ? Boolean(showBubble)
        : (settings.desktopBubblesEnabled === true);

    const data = {
        text: text,
        characterName: characterName,
        emotion: emotion ? (EMOTION_MAP[emotion.toLowerCase()] || emotion) : null,
        triggerLipSync: settings.autoLipSync !== false,
        showBubble: shouldShowBubble
    };
    const result = await sendToBridge('chat', data);

    // If bubble was requested and auto-dismiss is enabled, clear it after timeout
    if (shouldShowBubble && settings.autoDismissBubbles !== false) {
        scheduleBubbleDismiss(settings.bubbleDismissTimeoutSec || 5);
    }
    return result;
}

/**
 * Map SillyTavern expression label to MateEngine emotion
 */
function mapExpression(stExpression) {
    return EMOTION_MAP[stExpression?.toLowerCase()] || 'neutral';
}

/**
 * Simple text-based emotion detection as fallback
 * Analyzes action text (in asterisks) and message content
 */
function detectEmotionFromText(text) {
    if (!text) return null;

    const lowerText = text.toLowerCase();

    // Check for action descriptions in asterisks first (most reliable)
    const actionMatch = lowerText.match(/\*([^*]+)\*/);
    const actionText = actionMatch ? actionMatch[1] : '';

    // Joy/Happy patterns
    const joyPatterns = [
        'smil', 'laugh', 'giggl', 'chuckl', 'grin', 'beam', 'happy', 'joy',
        'excit', 'delight', 'cheer', 'amuse', 'pleased', 'thrill', 'elat',
        '😊', '😄', '😁', '🥰', '😍', '🤗', 'haha', 'hehe', 'lol'
    ];

    // Anger patterns
    const angerPatterns = [
        'angry', 'anger', 'frown', 'glare', 'scowl', 'furious', 'rage',
        'annoyed', 'irritat', 'frustrat', 'mad', 'snarl', 'growl',
        '😠', '😡', '🤬', 'grr'
    ];

    // Sadness patterns  
    const sorrowPatterns = [
        'sad', 'sigh', 'frown', 'tear', 'cry', 'sob', 'weep', 'melanchol',
        'depress', 'sorrow', 'downcast', 'gloom', 'mourn', 'griev',
        '😢', '😭', '😿', '🥺'
    ];

    // Fun/Love patterns
    const funPatterns = [
        'wink', 'playful', 'teas', 'flirt', 'love', 'adore', 'hug', 'cuddle',
        'affection', 'fond', 'blush', 'shy', 'coy', 'sweet',
        '😏', '😉', '💕', '💖', '❤️', '🥰'
    ];

    const searchText = actionText || lowerText;

    for (const pattern of joyPatterns) {
        if (searchText.includes(pattern)) return 'joy';
    }
    for (const pattern of angerPatterns) {
        if (searchText.includes(pattern)) return 'angry';
    }
    for (const pattern of sorrowPatterns) {
        if (searchText.includes(pattern)) return 'sorrow';
    }
    for (const pattern of funPatterns) {
        if (searchText.includes(pattern)) return 'fun';
    }

    return null;
}

/**
 * Detect action from text (jump, run, dance, etc)
 * Returns the animation name to play, or null if no action detected
 */
function detectActionFromText(text) {
    if (!text) return null;

    const lowerText = text.toLowerCase();

    // Check for action descriptions in asterisks first (most reliable)
    const actionMatch = lowerText.match(/\*([^*]+)\*/);
    const actionText = actionMatch ? actionMatch[1] : lowerText;

    // Check each action keyword
    for (const [keyword, animation] of Object.entries(ACTION_MAP)) {
        // Use word boundary matching to avoid false positives
        const regex = new RegExp(`\\b${keyword}\\b`, 'i');
        if (regex.test(actionText)) {
            console.log(`${DEBUG_PREFIX} Detected action: ${keyword} -> ${animation}`);
            return animation;
        }
    }

    return null;
}

/**
 * Play a custom animation (action)
 */
async function playAction(animationName, loop = false) {
    return sendToBridge('custom-animation', { animationName: animationName, loop });
}

/**
 * Handle new messages for auto-expression and lip sync
 */
async function onMessageReceived(messageId) {
    const context = getContext();
    const message = context.chat[messageId];

    if (!message || message.is_user || message.is_system) {
        return;
    }

    const settings = extension_settings[MODULE_NAME];
    if (!settings.enabled) return;

    // Get character name
    const characterName = message.name || context.characterName || 'Assistant';

    // Check for action first (jump, run, dance, etc) - these take priority
    const action = detectActionFromText(message.mes);
    if (action) {
        console.log(`${DEBUG_PREFIX} Playing action animation: ${action}`);
        await playAction(action, false);
    }

    // Get classified expression if available (from SillyTavern's expression extension)
    let emotion = null;
    if (message.extra?.classify?.label) {
        emotion = message.extra.classify.label;
        console.log(`${DEBUG_PREFIX} Using classified emotion: ${emotion}`);
    } else {
        // Fallback: detect emotion from text
        emotion = detectEmotionFromText(message.mes);
        if (emotion) {
            console.log(`${DEBUG_PREFIX} Detected emotion from text: ${emotion}`);
        }
    }

    // Send full chat message to MateEngine (includes lip sync, expression, and message display)
    // Strictly guard against interim typing placeholders ("...", "…", whitespace)
    if (message.mes && !isPlaceholder(message.mes)) {
        await sendChatMessage(message.mes, characterName, emotion);
    }
}

/**
 * Handle expression classification results
 */
async function onExpressionUpdated(data) {
    const settings = extension_settings[MODULE_NAME];
    if (!settings.enabled || !settings.autoSendExpressions) return;

    if (data && data.expression) {
        await setExpression(data.expression);
    }
}

/**
 * Logging helper
 */
function log(...args) {
    if (extension_settings[MODULE_NAME]?.debugLogging) {
        console.log(DEBUG_PREFIX, ...args);
    }
}

// ==================== SLASH COMMANDS ====================

/**
 * /mex expression [emotion] [value]
 */
function expressionCommand(args, value) {
    const emotion = value || args.emotion || 'neutral';
    const intensity = parseFloat(args.value || args.intensity || '1.0');
    setExpression(emotion, intensity);
    return `Set expression: ${emotion} (${intensity})`;
}

/**
 * /mex animation [param] [value]
 */
function animationCommand(args) {
    const param = args.param || args.animation;
    const value = args.value !== 'false';
    const isTrigger = args.trigger === 'true';

    if (!param) {
        return 'Usage: /mex animation param=name value=true/false';
    }

    setAnimation(param, value, isTrigger);
    return `Set animation: ${param} = ${value}`;
}

/**
 * /mex speak [text]
 */
function speakCommand(args, text) {
    const emotion = args.emotion;
    speak(text, emotion);
    return `Speaking: "${text.substring(0, 50)}..."`;
}

/**
 * /mex stop
 */
function stopCommand() {
    stopSpeaking();
    return 'Stopped speaking';
}

/**
 * /mex status
 */
async function statusCommand() {
    const status = await checkBridgeStatus();
    if (status) {
        return `MateEngine Bridge: Connected (port ${status.port})`;
    }
    return 'MateEngine Bridge: Not connected';
}

/**
 * Main slash command handler
 */
function mexCommand(args, value) {
    // Handle different SillyTavern versions - args._ may not exist
    const argsList = args?._ || [];
    const subcommand = (Array.isArray(argsList) && argsList.length > 0 ? argsList.shift() : null)
        || value?.split(' ')[0]
        || 'status';

    switch (subcommand.toLowerCase()) {
        case 'expression':
        case 'exp':
        case 'e':
            return expressionCommand(args, argsList.join(' ') || value?.split(' ').slice(1).join(' '));

        case 'animation':
        case 'anim':
        case 'a':
            return animationCommand(args);

        case 'speak':
        case 's':
            return speakCommand(args, argsList.join(' ') || value?.split(' ').slice(1).join(' '));

        case 'stop':
            return stopCommand();

        case 'status':
            return statusCommand();

        // Direct emotion shortcuts
        case 'joy':
        case 'happy':
            setExpression('joy');
            return 'Expression: Joy';

        case 'angry':
        case 'anger':
            setExpression('angry');
            return 'Expression: Angry';

        case 'sad':
        case 'sorrow':
            setExpression('sorrow');
            return 'Expression: Sorrow';

        case 'fun':
        case 'love':
            setExpression('fun');
            return 'Expression: Fun';

        case 'neutral':
            setExpression('neutral');
            return 'Expression: Neutral';

        // Action shortcuts
        case 'jump':
            playAction('action_jump');
            return 'Action: Jump';

        case 'run':
            playAction('action_run');
            return 'Action: Run';

        case 'walk':
            playAction('action_walk');
            return 'Action: Walk';

        case 'dance':
            playAction('dance_1');
            return 'Action: Dance';

        case 'wave':
        case 'greet':
            playAction('action_greeting');
            return 'Action: Wave';

        case 'crouch':
        case 'sit':
            playAction('action_crouch');
            return 'Action: Crouch';

        case 'lay':
        case 'lie':
            playAction('action_laydown');
            return 'Action: Lay down';

        default:
            // Check if it's an action keyword
            const actionAnim = ACTION_MAP[subcommand.toLowerCase()];
            if (actionAnim) {
                playAction(actionAnim);
                return `Action: ${subcommand}`;
            }
            // Assume it's an expression name
            setExpression(subcommand);
            return `Expression: ${subcommand}`;
    }
}

// ==================== UI ====================

function createSettingsUI() {
    const html = `
    <div class="mateengine-settings">
        <div class="inline-drawer">
            <div class="inline-drawer-toggle inline-drawer-header">
                <b>MateEngine Bridge</b>
                <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
            </div>
            <div class="inline-drawer-content">
                <div class="mateengine-settings-content">
                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_enabled" />
                        <span>Enable Bridge</span>
                    </label>
                    
                    <label>
                        Bridge URL:
                        <input type="text" id="mex_bridge_url" class="text_pole" placeholder="http://localhost:8787" />
                    </label>
                    
                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_auto_expressions" />
                        <span>Auto-send expressions</span>
                    </label>
                    
                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_auto_lipsync" />
                        <span>Auto lip-sync on messages</span>
                    </label>
                    
                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_debug" />
                        <span>Debug logging</span>
                    </label>
                    
                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_in_chat_poke" />
                        <span>Pipe spontaneous thoughts into active chat</span>
                    </label>

                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_desktop_bubbles" />
                        <span>Enable desktop speech bubbles (:8787)</span>
                    </label>

                    <label class="checkbox_label">
                        <input type="checkbox" id="mex_auto_dismiss_bubbles" />
                        <span>Auto-dismiss desktop speech bubbles</span>
                    </label>

                    <label class="text_pole_label">
                        <span>Bubble dismiss timeout (seconds)</span>
                        <input type="number" id="mex_dismiss_timeout" class="text_pole" min="2" max="60" step="1" placeholder="5" />
                    </label>
                    
                    <div class="mex-status" id="mex_status">
                        <span class="mex-status-indicator"></span>
                        <span class="mex-status-text">Checking...</span>
                    </div>
                    
                    <button id="mex_test_connection" class="menu_button">
                        Test Connection
                    </button>
                </div>
            </div>
        </div>
    </div>`;

    return $(html);
}

function bindSettingsUI() {
    const settings = extension_settings[MODULE_NAME];

    $('#mex_enabled').prop('checked', settings.enabled).on('change', function () {
        settings.enabled = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_bridge_url').val(settings.bridgeUrl).on('change', function () {
        settings.bridgeUrl = $(this).val();
        saveSettingsDebounced();
    });

    $('#mex_auto_expressions').prop('checked', settings.autoSendExpressions).on('change', function () {
        settings.autoSendExpressions = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_auto_lipsync').prop('checked', settings.autoLipSync).on('change', function () {
        settings.autoLipSync = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_debug').prop('checked', settings.debugLogging).on('change', function () {
        settings.debugLogging = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_in_chat_poke').prop('checked', settings.inChatPokeEnabled !== false).on('change', function () {
        settings.inChatPokeEnabled = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_desktop_bubbles').prop('checked', settings.desktopBubblesEnabled === true).on('change', function () {
        settings.desktopBubblesEnabled = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_auto_dismiss_bubbles').prop('checked', settings.autoDismissBubbles !== false).on('change', function () {
        settings.autoDismissBubbles = $(this).prop('checked');
        saveSettingsDebounced();
    });

    $('#mex_dismiss_timeout').val(settings.bubbleDismissTimeoutSec || 5).on('change', function () {
        const val = parseInt($(this).val(), 10);
        settings.bubbleDismissTimeoutSec = isNaN(val) ? 5 : Math.max(1, val);
        saveSettingsDebounced();
    });

    $('#mex_test_connection').on('click', async function () {
        const status = await checkBridgeStatus();
        const statusEl = $('#mex_status');

        if (status) {
            statusEl.find('.mex-status-indicator').css('background', '#4CAF50');
            statusEl.find('.mex-status-text').text(`Connected (port ${status.port})`);
        } else {
            statusEl.find('.mex-status-indicator').css('background', '#f44336');
            statusEl.find('.mex-status-text').text('Not connected');
        }
    });
}

// ==================== INITIALIZATION ====================

jQuery(async () => {
    loadSettings();

    // Add settings UI
    const settingsHtml = createSettingsUI();
    $('#extensions_settings2').append(settingsHtml);
    bindSettingsUI();

    // Register slash commands (if available)
    if (registerSlashCommand) {
        registerSlashCommand('mex', mexCommand, ['mateengine'],
            '<span class="monospace">[subcommand] [args]</span> – Control MateEngine avatar. Subcommands: expression, animation, speak, stop, status, joy, angry, sad, neutral',
            true, true);
    } else {
        console.log('[MateEngine] Slash commands not registered - function not available');
    }


    // Subscribe to events
    eventSource.on(event_types.MESSAGE_RECEIVED, onMessageReceived);
    if (event_types.CHARACTER_MESSAGE_RENDERED) {
        eventSource.on(event_types.CHARACTER_MESSAGE_RENDERED, () => { flushPendingPokes(); });
    }
    if (event_types.CHAT_CHANGED) {
        eventSource.on(event_types.CHAT_CHANGED, () => { flushPendingPokes(); });
    }

    // Check initial connection
    setTimeout(async () => {
        const status = await checkBridgeStatus();
        const statusEl = $('#mex_status');
        if (status) {
            statusEl.find('.mex-status-indicator').css('background', '#4CAF50');
            statusEl.find('.mex-status-text').text(`Connected (port ${status.port})`);
        } else {
            statusEl.find('.mex-status-indicator').css('background', '#888');
            statusEl.find('.mex-status-text').text('Not connected');
        }
    }, 2000);

    // Start polling for chat requests from server plugin
    startChatPolling();

    console.log(`${DEBUG_PREFIX} Extension loaded`);
});

// Poll interval ID
let pollIntervalId = null;
let isProcessingChat = false;

/**
 * Start polling for chat requests and spontaneous thoughts
 */
function startChatPolling() {
    if (pollIntervalId) return;

    pollIntervalId = setInterval(async () => {
        if (!extension_settings[MODULE_NAME].enabled || isProcessingChat) return;

        try {
            isProcessingChat = true;
            await processPendingChats();
            await processSpontaneousThoughts();
        } catch (error) {
            if (extension_settings[MODULE_NAME].debugLogging) {
                console.log(`${DEBUG_PREFIX} Poll error:`, error.message);
            }
        } finally {
            isProcessingChat = false;
        }
    }, 250); // Fast 250ms polling

    console.log(`${DEBUG_PREFIX} Started fast chat & spontaneous thought polling`);
}

let pendingInChatPokes = [];

/**
 * Checks whether SillyTavern is currently generating an LLM response.
 */
function isCurrentlyGenerating() {
    try {
        if (typeof isGenerating === 'function') return isGenerating();
    } catch (_) {}
    return false;
}

/**
 * Robustly sanitizes raw thought strings:
 * - Strips pipe alternatives (picks first non-empty option)
 * - Strips bracketed system/context tokens ([Context: ...], [Note: ...], etc.)
 * - Strips character prefix tags ('Alpha:', 'thought:', etc.) and surrounding quotes
 * - Strips non-BMP characters to prevent box glyphs
 */
function cleanSpontaneousText(rawText) {
    if (!rawText || typeof rawText !== 'string') return '';
    let text = rawText.trim();
    // 1. Pick first option if piped
    if (text.includes('|')) {
        const parts = text.split('|').map(s => s.trim()).filter(Boolean);
        if (parts.length > 0) text = parts[0];
    }
    // 2. Strip bracketed metadata or prompt tokens
    text = text.replace(/\[(?:Context|System|Note|Remark|Thinking|State):?[^\]]*\]/gi, '').trim();
    // 3. Strip common prefixes: 'Alpha:', 'Thought:', quotes
    text = text.replace(/^(?:Alpha|thought|remark):\s*/i, '').replace(/^["'“”‘’]|["'“”‘’]$/g, '').trim();
    // 4. Strip non-BMP characters
    text = text.replace(/[\uD800-\uDBFF][\uDC00-\uDFFF]/g, '').trim();
    return text;
}

/**
 * Flushes queued spontaneous remarks into SillyTavern chat once generation finishes or chat loads.
 */
async function flushPendingPokes() {
    if (isCurrentlyGenerating()) return;
    const context = getContext();
    if (!context || !context.chat || (context.characterId === undefined && !context.groupId)) return;

    while (pendingInChatPokes.length > 0 && !isCurrentlyGenerating()) {
        const item = pendingInChatPokes.shift();
        if (item && item.text) {
            await executeInChatInsertion(item.text, item.charName);
        }
    }
}

/**
 * Delivers a clean spontaneous thought into SillyTavern's active chat session
 * as an incoming character message turn without waiting for user input.
 */
async function deliverInChatPoke(thoughtText, characterName) {
    const cleanText = cleanSpontaneousText(thoughtText);
    if (!cleanText) return;

    if (isCurrentlyGenerating()) {
        console.log(`${DEBUG_PREFIX} Generation in progress — queued spontaneous thought for delivery: "${cleanText}"`);
        pendingInChatPokes.push({ text: cleanText, charName: characterName });
        return;
    }

    await executeInChatInsertion(cleanText, characterName);
}

/**
 * Directly appends message into SillyTavern active chat array, updates DOM, and saves.
 */
async function executeInChatInsertion(cleanText, characterName) {
    try {
        const context = getContext();
        if (!context || !context.chat || !Array.isArray(context.chat)) return;
        if (context.characterId === undefined && !context.groupId) {
            // Chat not yet loaded; retain in queue
            pendingInChatPokes.push({ text: cleanText, charName: characterName });
            return;
        }

        const charName = characterName
            || context.name2
            || (context.characters && context.characterId !== undefined ? context.characters[context.characterId]?.name : null)
            || 'Alpha';

        const message = {
            name: charName,
            is_user: false,
            is_system: false,
            send_date: new Date().toISOString(),
            mes: cleanText,
            extra: {
                isSpontaneous: true,
            },
        };

        // 1. Append directly to chat session
        context.chat.push(message);

        // 2. Render in chat DOM
        if (typeof addOneMessage === 'function') {
            addOneMessage(message);
        } else if (typeof context.addOneMessage === 'function') {
            context.addOneMessage(message);
        }

        const messageId = context.chat.length - 1;

        // 3. Emit SillyTavern character message events
        if (eventSource && eventSource.emit && event_types) {
            try {
                await eventSource.emit(event_types.MESSAGE_RECEIVED, messageId);
                await eventSource.emit(event_types.CHARACTER_MESSAGE_RENDERED, messageId);
            } catch (err) {
                console.warn(`${DEBUG_PREFIX} Error emitting in-chat poke events:`, err);
            }
        }

        // 4. Save updated chat history
        if (typeof saveChatConditional === 'function') {
            await saveChatConditional();
        } else if (typeof context.saveChat === 'function') {
            await context.saveChat();
        }

        console.log(`${DEBUG_PREFIX} \u2713 Spontaneous remark piped directly into active chat: "${cleanText}"`);
    } catch (e) {
        console.error(`${DEBUG_PREFIX} Failed to deliver in-chat poke:`, e);
    }
}

/**
 * Polls queued Spontaneous Thoughts from AnimeVision.py (port 5001)
 * and pipes them directly into SillyTavern active chat as character messages.
 * Prevents desktop bubble stacking by keeping showBubble: false.
 */
async function processSpontaneousThoughts() {
    try {
        // Check and flush any queued pokes once LLM generation has finished
        if (!isCurrentlyGenerating() && pendingInChatPokes.length > 0) {
            await flushPendingPokes();
        }

        const res = await fetch('http://127.0.0.1:5001/spontaneous_pop');
        if (!res.ok) return;
        const data = await res.json();
        if (!data) return;

        // Monitor chat box state transitions from Vision Sensor
        if (typeof data.chat_box_open === 'boolean') {
            const wasClosed = !lastKnownChatBoxOpen;
            lastKnownChatBoxOpen = data.chat_box_open;

            // If the chat box was reopened and user has desktop bubbles enabled, refresh bubble
            if (wasClosed && lastKnownChatBoxOpen && lastDeliveredResponse) {
                const ageMs = Date.now() - lastDeliveredResponse.timestamp;
                if (ageMs < 45000) {
                    const settings = extension_settings[MODULE_NAME] || {};
                    if (settings.desktopBubblesEnabled === true) {
                        await sendChatMessage(lastDeliveredResponse.text, lastDeliveredResponse.charName, 'joy', true);
                    }
                }
            }
        }

        if (!data.thought) return;

        const cleanText = cleanSpontaneousText(String(data.thought));
        if (!cleanText) return;

        const context = getContext();
        const characterName = context.characterName || 'Alpha';

        console.log(`${DEBUG_PREFIX} Ingested spontaneous thought from AnimeVision: "${cleanText}"`);

        // 1. Channel A: Animate 3D avatar & lip sync without stacking desktop speech bubbles
        await sendChatMessage(cleanText, characterName, 'joy', false);

        // 2. Channel B: Pipe ALL spontaneous thoughts directly into SillyTavern active chat
        const settings = extension_settings[MODULE_NAME] || {};
        if (settings.inChatPokeEnabled !== false) {
            await deliverInChatPoke(cleanText, characterName);
        }
    } catch (e) {
        // Vision server offline or busy
    }
}

/**
 * Process pending chat requests from server plugin (guaranteeing 0 placeholders)
 */
async function processPendingChats() {
    try {
        const response = await fetch('/api/plugins/mateengine-chat/pending');
        if (!response.ok) return;

        const data = await response.json();
        if (!data.pending || data.pending.length === 0) return;

        for (const request of data.pending) {
            console.log(`${DEBUG_PREFIX} Processing chat request ${request.requestId}: ${request.message.substring(0, 30)}...`);

            try {
                const claimResponse = await fetch('/api/plugins/mateengine-chat/claim', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ requestId: request.requestId })
                });
                const claimResult = await claimResponse.json();
                if (!claimResult.success) continue;

                const context = getContext();
                const chatLengthBefore = context.chat.length;

                // Check for short-term ambient poke context within 90s (Dual-Channel Memory)
                // Keep raw user message completely clean; inject ambient context as ephemeral system prompt
                const rawUserMessage = request.message;
                let ambientContextPrompt = '';
                try {
                    const pokeRes = await fetch('http://127.0.0.1:5001/last_ambient_poke');
                    if (pokeRes.ok) {
                        const pokeData = await pokeRes.json();
                        if (pokeData && pokeData.valid && pokeData.poke && pokeData.poke.text) {
                            const lastChatMsg = context.chat && context.chat.length > 0 ? context.chat[context.chat.length - 1] : null;
                            const pokeTextNorm = pokeData.poke.text.trim().toLowerCase();
                            const lastChatNorm = lastChatMsg ? String(lastChatMsg.mes || '').trim().toLowerCase() : '';

                            // Only inject ephemeral prompt if not already in chat history
                            if (!lastChatNorm.includes(pokeTextNorm)) {
                                console.log(`${DEBUG_PREFIX} Ambient poke context active:`, pokeData.poke.text);
                                ambientContextPrompt = `[Context: You recently teased or spoke to the user with: "${pokeData.poke.text}". The user is now responding to you.]`;
                                if (typeof setExtensionPrompt === 'function') {
                                    const promptType = (typeof extension_prompt_types !== 'undefined' && extension_prompt_types.IN_PROMPT !== undefined)
                                        ? extension_prompt_types.IN_PROMPT
                                        : 0;
                                    const promptRole = (typeof extension_prompt_roles !== 'undefined' && extension_prompt_roles.SYSTEM !== undefined)
                                        ? extension_prompt_roles.SYSTEM
                                        : 0;
                                    setExtensionPrompt('MateEngine_AmbientContext', ambientContextPrompt, promptType, 0, false, promptRole);
                                }
                            } else {
                                console.log(`${DEBUG_PREFIX} Ambient poke already in chat history — skipping redundant prompt injection.`);
                            }
                        }
                    }
                } catch (e) {
                    // Vision server offline or busy
                }

                // Send purely clean user message without prepended prompt leak
                await sendMessageAsUser(rawUserMessage, '');

                // Ensure UI display DOM strips out any injected prefix before saving to chat history
                try {
                    const chatEl = document.querySelector('#chat');
                    if (chatEl) {
                        const userMesNodes = chatEl.querySelectorAll('.mes[is_user="true"] .mes_text');
                        if (userMesNodes && userMesNodes.length > 0) {
                            const lastNode = userMesNodes[userMesNodes.length - 1];
                            if (lastNode && lastNode.textContent.includes('[Context:')) {
                                lastNode.innerHTML = lastNode.innerHTML.replace(/\[Context:[^\]]*\]\s*/g, '');
                            }
                        }
                    }
                } catch (domErr) {}

                console.log(`${DEBUG_PREFIX} Triggering AI generation and awaiting completion...`);
                // Await Generate('normal') and capture result directly
                const genResult = await Generate('normal');

                // Clear ephemeral extension prompt immediately after generation
                if (typeof setExtensionPrompt === 'function') {
                    try {
                        setExtensionPrompt('MateEngine_AmbientContext', '', 0, 0);
                    } catch (clearErr) {}
                }

                let aiResponse = null;
                const directText = (genResult !== null && genResult !== undefined) ? String(genResult).trim() : '';

                if (directText && !isPlaceholder(directText)) {
                    aiResponse = directText;
                }

                if (!aiResponse) {
                    // Poll chat context backwards up to 30 seconds for non-placeholder completion
                    for (let i = 0; i < 60; i++) {
                        const newContext = getContext();
                        if (newContext && Array.isArray(newContext.chat) && newContext.chat.length > chatLengthBefore) {
                            for (let idx = newContext.chat.length - 1; idx >= chatLengthBefore; idx--) {
                                const msg = newContext.chat[idx];
                                if (msg && !msg.is_user && !msg.is_system) {
                                    const candidate = (msg.mes || '').trim();
                                    if (candidate && !isPlaceholder(candidate)) {
                                        aiResponse = candidate;
                                        break;
                                    }
                                }
                            }
                            if (aiResponse) break;
                        }
                        await new Promise(resolve => setTimeout(resolve, 500));
                    }
                }

                // If still a placeholder or empty, fail gracefully instead of returning "..." to MateEngine
                if (!aiResponse || isPlaceholder(aiResponse)) {
                    console.warn(`${DEBUG_PREFIX} AI generation produced empty or placeholder response for request ${request.requestId}`);
                    await fetch('/api/plugins/mateengine-chat/response', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            requestId: request.requestId,
                            response: null,
                            error: 'AI response was empty or placeholder'
                        })
                    });
                } else {
                    console.log(`${DEBUG_PREFIX} Successfully resolved response for request ${request.requestId}: ${aiResponse.substring(0, 50)}...`);
                    await fetch('/api/plugins/mateengine-chat/response', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            requestId: request.requestId,
                            response: aiResponse,
                            error: null
                        })
                    });

                    lastDeliveredResponse = {
                        text: aiResponse,
                        charName: context.characterName || 'Alpha',
                        timestamp: Date.now()
                    };

                    try {
                        console.log(`${DEBUG_PREFIX} Dispatching response to MateEngine bridge (:8787)...`);
                        await sendChatMessage(aiResponse, context.characterName || 'Alpha');
                    } catch (dispatchErr) {
                        console.warn(`${DEBUG_PREFIX} Bridge dispatch error:`, dispatchErr.message);
                    }
                }

            } catch (error) {
                console.error(`${DEBUG_PREFIX} Error processing request ${request.requestId}:`, error);
                await fetch('/api/plugins/mateengine-chat/response', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        requestId: request.requestId,
                        response: null,
                        error: error.message
                    })
                });
            }
        }
    } catch (error) {
        if (extension_settings[MODULE_NAME].debugLogging) {
            console.log(`${DEBUG_PREFIX} Plugin not available:`, error.message);
        }
    }
}

/**
 * Switch SillyTavern character by name (called when MateEngine avatar changes)
 * @param {string} avatarName - Name of the avatar/character to switch to
 * @returns {object} - Result with success status and character name
 */
async function switchCharacterByName(avatarName) {
    if (!avatarName) {
        console.log(`${DEBUG_PREFIX} No avatar name provided for character switch`);
        return { success: false, message: 'No avatar name provided' };
    }

    const context = getContext();
    const characters = context.characters;

    if (!characters || characters.length === 0) {
        console.log(`${DEBUG_PREFIX} No characters available in SillyTavern`);
        return { success: false, message: 'No characters available' };
    }

    // Normalize avatar name (remove file extension if present)
    const normalizedName = avatarName.replace(/\.(vrm|me|prefab)$/i, '').toLowerCase().trim();

    console.log(`${DEBUG_PREFIX} Looking for character matching: "${normalizedName}"`);
    console.log(`${DEBUG_PREFIX} Available characters: ${characters.map(c => c.name).join(', ')}`);

    // Find character matching avatar name (case-insensitive)
    const match = characters.find(c =>
        c.name && c.name.toLowerCase().trim() === normalizedName
    );

    if (match) {
        console.log(`${DEBUG_PREFIX} Found matching character: ${match.name} (avatar: ${match.avatar})`);
        try {
            const characterId = characters.indexOf(match);
            console.log(`${DEBUG_PREFIX} Character ID/index: ${characterId}`);

            // Method 1: Try SillyTavern's selectCharacterById
            if (typeof context.selectCharacterById === 'function') {
                console.log(`${DEBUG_PREFIX} Using selectCharacterById(${characterId})`);
                await context.selectCharacterById(characterId);
                console.log(`${DEBUG_PREFIX} ✓ Switched to character: ${match.name}`);
                return { success: true, character: match.name };
            }

            // Method 2: Try clicking the character element in the UI
            const charElement = document.querySelector(`.character_select[chid="${characterId}"]`);
            if (charElement) {
                console.log(`${DEBUG_PREFIX} Using UI click fallback`);
                charElement.click();
                console.log(`${DEBUG_PREFIX} ✓ Clicked to switch to character: ${match.name}`);
                return { success: true, character: match.name };
            }

            // Method 3: Try using the avatar filename
            if (match.avatar) {
                const avatarElement = document.querySelector(`.character_select[file_name="${match.avatar}"]`);
                if (avatarElement) {
                    console.log(`${DEBUG_PREFIX} Using avatar filename click fallback`);
                    avatarElement.click();
                    console.log(`${DEBUG_PREFIX} ✓ Clicked avatar to switch to character: ${match.name}`);
                    return { success: true, character: match.name };
                }
            }

            console.log(`${DEBUG_PREFIX} ✗ No switching method available`);
            return { success: false, message: 'No switching method available' };
        } catch (error) {
            console.error(`${DEBUG_PREFIX} Error switching character:`, error);
            return { success: false, message: error.message };
        }
    }

    console.log(`${DEBUG_PREFIX} ✗ No character found matching: "${avatarName}" (normalized: "${normalizedName}")`);
    return { success: false, message: `Character "${avatarName}" not found` };
}

/**
 * Poll for avatar change requests from MateEngine
 */
let lastSyncedAvatar = null; // Track last synced avatar to prevent redundant switches

async function pollAvatarChanges() {
    try {
        const url = `${extension_settings[MODULE_NAME].bridgeUrl}/api/avatar-changed-pending`;
        const response = await fetch(url);

        if (!response.ok) return;

        const data = await response.json();
        if (data && data.avatarName) {
            // Check if this is a first-time notification (pending) or a refresh sync
            const isPending = data.isPending !== false; // Default to true for backwards compat

            // Skip if this is a refresh sync and we already synced to this avatar
            if (!isPending && lastSyncedAvatar === data.avatarName) {
                return; // Already synced, no need to switch again
            }

            console.log(`${DEBUG_PREFIX} Avatar change: ${data.avatarName} (isPending: ${isPending})`);
            const result = await switchCharacterByName(data.avatarName);

            // Remember this avatar so we don't redundantly switch on next poll
            if (result.success) {
                lastSyncedAvatar = data.avatarName;
            }

            // Only acknowledge if it was a pending (first-time) notification
            // Don't ack for refresh syncs to avoid unnecessary processing
            if (isPending) {
                await fetch(`${extension_settings[MODULE_NAME].bridgeUrl}/api/avatar-changed-ack`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ acknowledged: true, result })
                });
            }
        }
    } catch (error) {
        // Silently fail - bridge may not be running
    }
}

// Start polling for avatar changes (every 2 seconds)
setInterval(pollAvatarChanges, 2000);

// Export for other extensions
window.MateEngineBridge = {
    setExpression,
    setAnimation,
    speak,
    stopSpeaking,
    sendChatMessage,
    checkBridgeStatus,
    mapExpression,
    processPendingChats,
    switchCharacterByName,
};
