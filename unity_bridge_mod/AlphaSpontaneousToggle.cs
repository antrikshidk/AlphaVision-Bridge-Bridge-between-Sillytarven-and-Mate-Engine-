using System;
using System.Text;
using TMPro;
using UnityEngine;
using UnityEngine.Events;
using UnityEngine.Networking;
using UnityEngine.UI;

public class AlphaSpontaneousToggle : MonoBehaviour
{
    private const string EndpointUrl = "http://127.0.0.1:5001/api/spontaneous";
    private const string ToggleLabel = "SPONTANEOUS REMARKS";

    private Toggle targetToggle;
    private bool isSyncingFromServer;

    public static void AttachBelow(Toggle templateToggle)
    {
        try
        {
            if (templateToggle == null) return;
            Transform parent = templateToggle.transform.parent;
            if (parent == null) return;

            Transform existing = parent.Find("SpontaneousRemarksToggle");
            if (existing != null) return;

            GameObject clone = UnityEngine.Object.Instantiate(templateToggle.gameObject, parent, false);
            clone.name = "SpontaneousRemarksToggle";
            PlaceCloneAfter(parent, templateToggle.transform, clone.transform);

            StripLocalization(clone);
            ApplyToggleLabel(clone);
            AdjustLayoutIfManual(parent, templateToggle.GetComponent<RectTransform>(), clone.GetComponent<RectTransform>());

            Toggle clonedToggle = clone.GetComponent<Toggle>();
            if (clonedToggle == null)
            {
                clonedToggle = clone.GetComponentInChildren<Toggle>(true);
            }
            if (clonedToggle == null)
            {
                Debug.LogWarning("[AlphaSpontaneousToggle] Cloned object had no Toggle component.");
                return;
            }

            // Replace ToggleEvent completely so serialized Inspector DiscordRPC listeners are detached
            clonedToggle.onValueChanged = new Toggle.ToggleEvent();

            AlphaSpontaneousToggle ctrl = clone.AddComponent<AlphaSpontaneousToggle>();
            ctrl.Initialize(clonedToggle);
            Debug.Log("[AlphaSpontaneousToggle] Attached SPONTANEOUS REMARKS toggle below DISCORD RICH PRESENCE.");
        }
        catch (Exception ex)
        {
            Debug.LogError("[AlphaSpontaneousToggle] AttachBelow failed: " + ex);
        }
    }

    private static void PlaceCloneAfter(Transform parent, Transform templateTf, Transform cloneTf)
    {
        if (parent == null || templateTf == null || cloneTf == null) return;
        int targetIdx = templateTf.GetSiblingIndex();
        int prefixCount = targetIdx + 1;
        Transform[] prefix = new Transform[prefixCount];
        for (int i = 0; i < prefixCount; i++)
        {
            prefix[i] = parent.GetChild(i);
        }
        // Push clone first, then template down to index 0 using SetAsFirstSibling (survives Unity stripping)
        cloneTf.SetAsFirstSibling();
        for (int i = prefixCount - 1; i >= 0; i--)
        {
            if (prefix[i] != null && prefix[i] != cloneTf)
            {
                prefix[i].SetAsFirstSibling();
            }
        }
    }

    private static void StripLocalization(GameObject root)
    {
        MonoBehaviour[] comps = root.GetComponentsInChildren<MonoBehaviour>(true);
        for (int i = 0; i < comps.Length; i++)
        {
            MonoBehaviour mb = comps[i];
            if (mb == null) continue;
            string tName = mb.GetType().Name;
            if (tName.IndexOf("Localiz", StringComparison.OrdinalIgnoreCase) >= 0 ||
                tName.IndexOf("Translate", StringComparison.OrdinalIgnoreCase) >= 0 ||
                tName.IndexOf("I2", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                UnityEngine.Object.Destroy(mb);
            }
        }
    }

    private static void ApplyToggleLabel(GameObject root)
    {
        TMP_Text[] tmpTexts = root.GetComponentsInChildren<TMP_Text>(true);
        for (int i = 0; i < tmpTexts.Length; i++)
        {
            if (tmpTexts[i] != null)
            {
                tmpTexts[i].text = ToggleLabel;
            }
        }

        Text[] uiTexts = root.GetComponentsInChildren<Text>(true);
        for (int i = 0; i < uiTexts.Length; i++)
        {
            if (uiTexts[i] != null)
            {
                uiTexts[i].text = ToggleLabel;
            }
        }
    }

    private static void AdjustLayoutIfManual(Transform parent, RectTransform tplRt, RectTransform cloneRt)
    {
        RectTransform parentRt = parent as RectTransform;
        LayoutGroup layoutGroup = parent.GetComponent<LayoutGroup>();
        if (layoutGroup != null)
        {
            if (parentRt != null)
            {
                LayoutRebuilder.ForceRebuildLayoutImmediate(parentRt);
            }
            return;
        }

        if (tplRt == null || cloneRt == null) return;
        float rowHeight = tplRt.rect.height > 1f ? tplRt.rect.height : 28f;
        float step = rowHeight + 8f;
        float tplY = tplRt.anchoredPosition.y;

        for (int i = 0; i < parent.childCount; i++)
        {
            Transform child = parent.GetChild(i);
            if (child == cloneRt || child == tplRt) continue;
            RectTransform childRt = child as RectTransform;
            if (childRt != null && childRt.anchoredPosition.y < tplY - 1f)
            {
                Vector2 pos = childRt.anchoredPosition;
                pos.y -= step;
                childRt.anchoredPosition = pos;
            }
        }

        cloneRt.anchoredPosition = new Vector2(tplRt.anchoredPosition.x, tplY - step);

        if (parentRt != null && parent.GetComponent<ContentSizeFitter>() == null)
        {
            Vector2 size = parentRt.sizeDelta;
            size.y += step;
            parentRt.sizeDelta = size;
        }
    }

    public void Initialize(Toggle toggle)
    {
        targetToggle = toggle;
        if (targetToggle != null)
        {
            targetToggle.onValueChanged.AddListener(new UnityAction<bool>(OnToggleChanged));
        }
        StartSyncFromBackend();
    }

    private void OnEnable()
    {
        ApplyToggleLabel(gameObject);
        if (targetToggle != null)
        {
            StartSyncFromBackend();
        }
    }

    private void OnToggleChanged(bool newValue)
    {
        if (isSyncingFromServer) return;
        StartPostToBackend(newValue);
    }

    private void StartSyncFromBackend()
    {
        try
        {
            UnityWebRequest req = UnityWebRequest.Get(EndpointUrl);
            req.timeout = 3;
            UnityWebRequestAsyncOperation op = req.SendWebRequest();
            op.completed += new Action<AsyncOperation>(OnGetCompleted);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[AlphaSpontaneousToggle] GET sync error: " + ex.Message);
        }
    }

    private void OnGetCompleted(AsyncOperation asyncOp)
    {
        UnityWebRequestAsyncOperation uwrOp = asyncOp as UnityWebRequestAsyncOperation;
        if (uwrOp == null || uwrOp.webRequest == null) return;

        UnityWebRequest req = uwrOp.webRequest;
        try
        {
            if (req.result == UnityWebRequest.Result.Success && req.downloadHandler != null)
            {
                bool parsed;
                if (TryParseEnabledJson(req.downloadHandler.text, out parsed) && targetToggle != null)
                {
                    isSyncingFromServer = true;
                    targetToggle.SetIsOnWithoutNotify(parsed);
                    isSyncingFromServer = false;
                }
            }
        }
        catch (Exception ex)
        {
            isSyncingFromServer = false;
            Debug.LogWarning("[AlphaSpontaneousToggle] GET response parse failed: " + ex.Message);
        }
        finally
        {
            req.Dispose();
        }
    }

    private void StartPostToBackend(bool enabled)
    {
        try
        {
            string json = enabled ? "{\"enabled\":true}" : "{\"enabled\":false}";
            byte[] bodyRaw = Encoding.UTF8.GetBytes(json);

            UnityWebRequest req = new UnityWebRequest(EndpointUrl, "POST");
            req.uploadHandler = new UploadHandlerRaw(bodyRaw);
            req.downloadHandler = new DownloadHandlerBuffer();
            req.SetRequestHeader("Content-Type", "application/json");
            req.timeout = 4;

            UnityWebRequestAsyncOperation op = req.SendWebRequest();
            op.completed += new Action<AsyncOperation>(OnPostCompleted);
        }
        catch (Exception ex)
        {
            Debug.LogWarning("[AlphaSpontaneousToggle] POST error: " + ex.Message);
        }
    }

    private void OnPostCompleted(AsyncOperation asyncOp)
    {
        UnityWebRequestAsyncOperation uwrOp = asyncOp as UnityWebRequestAsyncOperation;
        if (uwrOp == null || uwrOp.webRequest == null) return;

        UnityWebRequest req = uwrOp.webRequest;
        try
        {
            if (req.result == UnityWebRequest.Result.Success)
            {
                Debug.Log("[AlphaSpontaneousToggle] Synced spontaneous remarks state: " +
                    (req.downloadHandler != null ? req.downloadHandler.text : "OK"));
            }
            else
            {
                Debug.LogWarning("[AlphaSpontaneousToggle] POST failed (" + req.responseCode + "): " + req.error);
            }
        }
        finally
        {
            req.Dispose();
        }
    }

    private static bool TryParseEnabledJson(string json, out bool enabled)
    {
        enabled = true;
        if (string.IsNullOrEmpty(json)) return false;
        string compact = json.Replace(" ", "").Replace("\n", "").Replace("\r", "").Replace("\t", "").ToLowerInvariant();
        if (compact.Contains("\"enabled\":false"))
        {
            enabled = false;
            return true;
        }
        if (compact.Contains("\"enabled\":true"))
        {
            enabled = true;
            return true;
        }
        return false;
    }
}
