/**
 * MateEngine Chat Server Plugin for SillyTavern
 *
 * Exposes an HTTP endpoint that allows MateEngine to send chat messages
 * and receive AI responses using SillyTavern's character cards and presets,
 * with automatic direct fallback to AlphaVision (:5001/v1/chat/completions)
 * whenever the SillyTavern browser tab isn't open/polling.
 *
 * Endpoint: POST /api/plugins/mateengine-chat/chat
 *
 * Request body: { "message": "user message text" }
 * Response: { "success": true, "response": "AI response text" }
 */

const http = require('node:http');

// Store for pending responses
const pendingResponses = new Map();
let responseIdCounter = 0;

const ALPHAVISION_HOST = process.env.ALPHAVISION_HOST || '127.0.0.1';
const ALPHAVISION_PORT = parseInt(process.env.FLASK_PORT || process.env.ALPHA_VISION_PORT || '5001', 10);
const BROWSER_CLAIM_GRACE_MS = 2500;

/**
 * Checks whether text is an interim or typing placeholder (e.g. "...", "…", whitespace)
 */
function isPlaceholderText(text) {
    if (!text || typeof text !== 'string') return true;
    const stripped = text.replace(/<[^>]*>/g, '').trim();
    return stripped === '' || /^[\.\s…]+$/.test(stripped) || stripped === '...' || stripped === '…';
}

/**
 * Direct fallback POST to AlphaVision (:5001/v1/chat/completions) when no
 * SillyTavern browser tab claims /pending within BROWSER_CLAIM_GRACE_MS.
 */
function forwardDirectToAlphaVision(message, timeoutMs = 35000) {
    return new Promise((resolve) => {
        const bodyStr = JSON.stringify({
            model: 'gpt-3.5-turbo',
            stream: false,
            messages: [{ role: 'user', content: message }],
        });

        const req = http.request(
            {
                hostname: ALPHAVISION_HOST,
                port: ALPHAVISION_PORT,
                path: '/v1/chat/completions',
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Content-Length': Buffer.byteLength(bodyStr),
                },
                timeout: timeoutMs,
            },
            (res) => {
                let raw = '';
                res.setEncoding('utf8');
                res.on('data', (chunk) => { raw += chunk; });
                res.on('end', () => {
                    try {
                        const parsed = JSON.parse(raw);
                        const reply = parsed?.choices?.[0]?.message?.content;
                        if (reply && !isPlaceholderText(reply)) {
                            return resolve({ success: true, response: reply.trim(), source: 'alphavision-direct' });
                        }
                        return resolve({ success: false, error: `AlphaVision returned empty content (HTTP ${res.statusCode})` });
                    } catch (parseErr) {
                        return resolve({ success: false, error: `Invalid AlphaVision JSON (${res.statusCode}): ${parseErr.message}` });
                    }
                });
            },
        );

        req.on('timeout', () => {
            req.destroy();
            resolve({ success: false, error: `AlphaVision fallback timed out after ${timeoutMs}ms` });
        });

        req.on('error', (err) => {
            resolve({ success: false, error: `AlphaVision unreachable (:5001): ${err.message}` });
        });

        req.write(bodyStr);
        req.end();
    });
}

/**
 * Initialize the plugin
 * @param {import('express').Router} router Express router
 * @returns {Promise<void>}
 */
async function init(router) {
    console.log('[MateEngine-Chat] Plugin initializing...');

    // Endpoint to get pending messages (for browser extension to pick up)
    router.get('/pending', (req, res) => {
        const pendingList = [];
        for (const [id, data] of pendingResponses.entries()) {
            // Only return unclaimed requests
            if (!data.claimed) {
                pendingList.push({
                    requestId: id,
                    message: data.userMessage,
                    timestamp: data.timestamp
                });
            }
        }
        res.json({ pending: pendingList });
    });

    // Endpoint to claim a request (marks it as being processed)
    router.post('/claim', (req, res) => {
        const { requestId } = req.body;
        const pending = pendingResponses.get(requestId);
        if (pending && !pending.claimed) {
            pending.claimed = true;
            res.json({ success: true });
        } else {
            res.json({ success: false, error: 'Already claimed or not found' });
        }
    });

    // Endpoint to submit AI response (called by browser extension)
    router.post('/response', (req, res) => {
        const { requestId, response, error } = req.body;

        if (!requestId) {
            return res.status(400).json({
                success: false,
                error: 'requestId is required'
            });
        }

        const pending = pendingResponses.get(requestId);
        if (!pending) {
            return res.status(404).json({
                success: false,
                error: 'Request not found or already completed'
            });
        }

        const isPlaceholder = isPlaceholderText(response);
        const isValid = !error && !isPlaceholder && response !== null && response !== undefined;

        if (isPlaceholder && !error) {
            console.warn(`[MateEngine-Chat] Request ${requestId} submitted placeholder response ('${response}'), rejecting.`);
        }

        if (pending.fallbackTimer) clearTimeout(pending.fallbackTimer);
        if (pending.timeoutTimer) clearTimeout(pending.timeoutTimer);

        // Resolve the pending promise
        pending.resolver({
            success: isValid,
            response: isValid ? response : null,
            error: error || (isPlaceholder ? 'Placeholder response rejected' : null)
        });

        pendingResponses.delete(requestId);

        res.json({ success: true, rejectedPlaceholder: isPlaceholder });
    });

    // Main chat endpoint - waits for browser extension, falls back directly to :5001 if unclaimed
    router.post('/chat', async (req, res) => {
        try {
            const { message, timeout = 120000 } = req.body;

            if (!message || typeof message !== 'string') {
                return res.status(400).json({
                    success: false,
                    error: 'Message is required and must be a string'
                });
            }

            console.log(`[MateEngine-Chat] Chat request: ${message.substring(0, 50)}...`);

            const requestId = ++responseIdCounter;

            let responseResolver;
            const responsePromise = new Promise((resolve) => {
                responseResolver = resolve;
            });

            const timeoutTimer = setTimeout(() => {
                const entry = pendingResponses.get(requestId);
                if (entry) {
                    if (entry.fallbackTimer) clearTimeout(entry.fallbackTimer);
                    pendingResponses.delete(requestId);
                    resolveTimeout(entry);
                }
            }, timeout);

            function resolveTimeout(entry) {
                entry.resolver({
                    success: false,
                    error: 'Timeout waiting for AI response. Make sure SillyTavern browser or AlphaVision (:5001) is running.'
                });
            }

            const fallbackTimer = setTimeout(async () => {
                const entry = pendingResponses.get(requestId);
                if (!entry || entry.claimed) {
                    return;
                }
                // Browser extension did not claim within grace window -> route straight to AlphaVision (:5001)
                console.warn(`[MateEngine-Chat] Request ${requestId} unclaimed after ${BROWSER_CLAIM_GRACE_MS}ms — routing directly to AlphaVision :${ALPHAVISION_PORT}`);
                entry.claimed = true;
                pendingResponses.delete(requestId);
                clearTimeout(entry.timeoutTimer);
                const directResult = await forwardDirectToAlphaVision(message, Math.min(45000, Math.max(15000, timeout - BROWSER_CLAIM_GRACE_MS)));
                entry.resolver(directResult);
            }, BROWSER_CLAIM_GRACE_MS);

            pendingResponses.set(requestId, {
                resolver: responseResolver,
                userMessage: message,
                timestamp: Date.now(),
                claimed: false,
                fallbackTimer,
                timeoutTimer,
            });

            // Wait for browser extension or direct AlphaVision fallback
            const result = await responsePromise;

            if (result.success) {
                console.log(`[MateEngine-Chat] Response received (${result.source || 'sillytavern-tab'}): ${(result.response || '').substring(0, 50)}...`);
            } else {
                console.log(`[MateEngine-Chat] Error: ${result.error}`);
            }

            res.json(result);

        } catch (error) {
            console.error('[MateEngine-Chat] Chat error:', error);
            res.status(500).json({
                success: false,
                error: error.message
            });
        }
    });

    // Health check
    router.get('/status', (req, res) => {
        res.json({
            success: true,
            plugin: 'mateengine-chat',
            version: '1.1.0',
            pendingRequests: pendingResponses.size,
            alphaVisionFallback: `http://${ALPHAVISION_HOST}:${ALPHAVISION_PORT}/v1/chat/completions`
        });
    });

    console.log('[MateEngine-Chat] Plugin loaded!');
    console.log(`[MateEngine-Chat] Direct fallback enabled -> http://${ALPHAVISION_HOST}:${ALPHAVISION_PORT}/v1/chat/completions`);
}

async function exit() {
    console.log('[MateEngine-Chat] Plugin shutting down...');
    for (const entry of pendingResponses.values()) {
        if (entry.fallbackTimer) clearTimeout(entry.fallbackTimer);
        if (entry.timeoutTimer) clearTimeout(entry.timeoutTimer);
    }
    pendingResponses.clear();
}

module.exports = {
    init,
    exit,
    info: {
        id: 'mateengine-chat',
        name: 'MateEngine Chat',
        description: 'Allows MateEngine desktop app to chat using SillyTavern character cards and presets with direct AlphaVision fallback',
    },
};
