/**
 * MateEngine Chat Server Plugin for SillyTavern
 * 
 * Exposes an HTTP endpoint that allows MateEngine to send chat messages
 * and receive AI responses using SillyTavern's character cards and presets.
 * 
 * Endpoint: POST /api/plugins/mateengine-chat/chat
 * 
 * Request body: { "message": "user message text" }
 * Response: { "success": true, "response": "AI response text" }
 */

// Store for pending responses
const pendingResponses = new Map();
let responseIdCounter = 0;

/**
 * Checks whether text is an interim or typing placeholder (e.g. "...", "…", whitespace)
 */
function isPlaceholderText(text) {
    if (!text || typeof text !== 'string') return true;
    const stripped = text.replace(/<[^>]*>/g, '').trim();
    return stripped === '' || /^[\.\s…]+$/.test(stripped) || stripped === '...' || stripped === '…';
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

        // Resolve the pending promise
        pending.resolver({
            success: isValid,
            response: isValid ? response : null,
            error: error || (isPlaceholder ? 'Placeholder response rejected' : null)
        });

        pendingResponses.delete(requestId);

        res.json({ success: true, rejectedPlaceholder: isPlaceholder });
    });

    // Main chat endpoint - waits for browser extension response
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
                setTimeout(() => {
                    if (pendingResponses.has(requestId)) {
                        pendingResponses.delete(requestId);
                        resolve({
                            success: false,
                            error: 'Timeout waiting for AI response. Make sure SillyTavern browser is open with MateEngine extension.'
                        });
                    }
                }, timeout);
            });

            pendingResponses.set(requestId, {
                resolver: responseResolver,
                userMessage: message,
                timestamp: Date.now()
            });

            // Wait for browser extension to pick up and respond
            const result = await responsePromise;

            if (result.success) {
                console.log(`[MateEngine-Chat] Response received: ${(result.response || '').substring(0, 50)}...`);
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
            version: '1.0.0',
            pendingRequests: pendingResponses.size
        });
    });

    console.log('[MateEngine-Chat] Plugin loaded!');
    console.log('[MateEngine-Chat] Endpoints: /api/plugins/mateengine-chat/chat, /pending, /response, /status');
}

async function exit() {
    console.log('[MateEngine-Chat] Plugin shutting down...');
    pendingResponses.clear();
}

module.exports = {
    init,
    exit,
    info: {
        id: 'mateengine-chat',
        name: 'MateEngine Chat',
        description: 'Allows MateEngine desktop app to chat using SillyTavern character cards and presets',
    },
};
