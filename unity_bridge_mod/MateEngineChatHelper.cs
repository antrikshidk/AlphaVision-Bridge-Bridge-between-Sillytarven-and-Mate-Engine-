using System;
using UnityEngine;
using LLMUnitySamples;

public static class MateEngineChatHelper
{
    public static void NormalizeChatRequest(MateEngineBridge.ChatRequest data)
    {
        if (data == null) return;
        if (string.IsNullOrEmpty(data.text) && !string.IsNullOrEmpty(data.message))
        {
            data.text = data.message;
        }
        if (string.IsNullOrEmpty(data.characterName) && !string.IsNullOrEmpty(data.sender))
        {
            data.characterName = data.sender;
        }
        if (!data.showBubble && (data.is_bubble || data.add_to_chat))
        {
            data.showBubble = true;
        }
        if (string.IsNullOrEmpty(data.text) && !data.showBubble)
        {
            try
            {
                if (MateEngineBridge.Instance != null)
                {
                    MateEngineBridge.Instance.ForceStandUp();
                }
            }
            catch (Exception ex)
            {
                Debug.LogWarning("[MateEngineBridge] ForceStandUp in NormalizeChatRequest: " + ex.Message);
            }
        }
    }

    public static void EnsureChatBotResponse(string cleanedText)
    {
        try
        {
            if (string.IsNullOrEmpty(cleanedText)) return;
            ChatBot chatBot = UnityEngine.Object.FindFirstObjectByType<ChatBot>(FindObjectsInactive.Include);
            if (chatBot != null)
            {
                if (!chatBot.gameObject.activeInHierarchy)
                {
                    chatBot.gameObject.SetActive(true);
                }
                chatBot.DisplayAIResponse(cleanedText);
                Debug.Log("[MateEngineBridge] Autonomous ChatBot.DisplayAIResponse dispatched: " + cleanedText);
            }
            else
            {
                Debug.LogWarning("[MateEngineBridge] ChatBot component not found even with FindObjectsInactive.Include");
            }
        }
        catch (Exception ex)
        {
            Debug.LogError("[MateEngineBridge] Error in EnsureChatBotResponse: " + ex.Message + "\n" + ex.StackTrace);
        }
    }
}
