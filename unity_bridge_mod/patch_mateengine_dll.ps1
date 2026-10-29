$ErrorActionPreference = "Stop"
Add-Type -Path "C:\DriveD\dnSpy-netframework\bin\dnlib.dll"

$managedDir = "C:\DriveD\MateEngineWin3.2 modded\MateEngineX_Data\Managed"
$srcDll = Join-Path $managedDir "Assembly-CSharp.dll.before_patch"
$targetDll = Join-Path $managedDir "Assembly-CSharp.dll"
$corePath = Join-Path $managedDir "UnityEngine.CoreModule.dll"

Write-Host "Step 1: Loading clean base Assembly-CSharp.dll.before_patch..."
$mod = [dnlib.DotNet.ModuleDefMD]::Load($srcDll)
$coreMod = [dnlib.DotNet.ModuleDefMD]::Load($corePath)

$bridge = $mod.Find("MateEngineBridge", $true)
$chatReq = $bridge.NestedTypes | Where-Object { $_.Name -eq "ChatRequest" }

# Add fields to ChatRequest
$stringSig = $mod.CorLibTypes.String
$boolSig = $mod.CorLibTypes.Boolean

if (-not ($chatReq.Fields | Where-Object { $_.Name -eq "message" })) {
    $chatReq.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("message", (New-Object dnlib.DotNet.FieldSig($stringSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'message'"
}
if (-not ($chatReq.Fields | Where-Object { $_.Name -eq "sender" })) {
    $chatReq.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("sender", (New-Object dnlib.DotNet.FieldSig($stringSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'sender'"
}
if (-not ($chatReq.Fields | Where-Object { $_.Name -eq "is_bubble" })) {
    $chatReq.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("is_bubble", (New-Object dnlib.DotNet.FieldSig($boolSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'is_bubble'"
}
if (-not ($chatReq.Fields | Where-Object { $_.Name -eq "add_to_chat" })) {
    $chatReq.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("add_to_chat", (New-Object dnlib.DotNet.FieldSig($boolSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'add_to_chat'"
}

Write-Host "Step 2: Patching HandleChat fallback normalization..."
$handleChat = $bridge.FindMethod("HandleChat")
$displayClass = $bridge.NestedTypes | Where-Object { $_.Name -like "*DisplayClass42_0*" }
$dataField = $displayClass.Fields | Where-Object { $_.Name -eq "data" }
$textField = $chatReq.Fields | Where-Object { $_.Name -eq "text" }
$messageField = $chatReq.Fields | Where-Object { $_.Name -eq "message" }
$charNameField = $chatReq.Fields | Where-Object { $_.Name -eq "characterName" }
$senderField = $chatReq.Fields | Where-Object { $_.Name -eq "sender" }
$showBubbleField = $chatReq.Fields | Where-Object { $_.Name -eq "showBubble" }
$isBubbleField = $chatReq.Fields | Where-Object { $_.Name -eq "is_bubble" }
$addToChatField = $chatReq.Fields | Where-Object { $_.Name -eq "add_to_chat" }

# Grab existing IsNullOrEmpty MemberRef
$isNullOrEmpty = $handleChat.Body.Instructions[12].Operand

$lblCheckSender = [dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction()
$lblCheckBubble = [dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction()
$lblSetBubble = [dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction()
$lblDone = $handleChat.Body.Instructions[9]

$normInsts = New-Object System.Collections.Generic.List[dnlib.DotNet.Emit.Instruction]

# Part 1: text fallback to message
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($textField))
$normInsts.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$isNullOrEmpty))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brfalse.ToInstruction($lblCheckSender))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($messageField))
$normInsts.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$isNullOrEmpty))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblCheckSender))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($messageField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Stfld.ToInstruction($textField))

# Part 2: characterName fallback to sender
$normInsts.Add($lblCheckSender)
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($charNameField))
$normInsts.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$isNullOrEmpty))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brfalse.ToInstruction($lblCheckBubble))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($senderField))
$normInsts.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$isNullOrEmpty))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblCheckBubble))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($senderField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Stfld.ToInstruction($charNameField))

# Part 3: showBubble fallback to is_bubble || add_to_chat
$normInsts.Add($lblCheckBubble)
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($showBubbleField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblDone))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($isBubbleField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblSetBubble))

$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($addToChatField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brfalse.ToInstruction($lblDone))

$normInsts.Add($lblSetBubble)
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField))
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldc_I4_1.ToInstruction())
$normInsts.Add([dnlib.DotNet.Emit.OpCodes]::Stfld.ToInstruction($showBubbleField))

for ($i = 0; $i -lt $normInsts.Count; $i++) {
    $handleChat.Body.Instructions.Insert(9 + $i, $normInsts[$i])
}
$handleChat.Body.SimplifyBranches()
$handleChat.Body.OptimizeBranches()
$handleChat.Body.OptimizeMacros()
Write-Host "  HandleChat successfully patched with $($normInsts.Count) instructions."

Write-Host "Step 3: Patching ProcessChatMessage to include inactive ChatBot and activate hierarchy..."
$procChat = $bridge.FindMethod("ProcessChatMessage")
$ffIdx = -1
for ($i = 0; $i -lt $procChat.Body.Instructions.Count; $i++) {
    $inst = $procChat.Body.Instructions[$i]
    if ($inst.Operand -ne $null -and $inst.Operand.ToString() -like "*FindFirstObjectByType*") {
        $ffIdx = $i
        break
    }
}
Write-Host "  FindFirstObjectByType found at index $ffIdx"

# Build MethodSpec for FindFirstObjectByType<ChatBot>(FindObjectsInactive)
$objType = $coreMod.Find("UnityEngine.Object", $true)
$findMethod = $objType.Methods | Where-Object { 
    $_.Name -eq "FindFirstObjectByType" -and 
    $_.GenericParameters.Count -eq 1 -and 
    $_.Parameters.Count -eq 1 -and 
    $_.Parameters[0].Type.TypeName -eq "FindObjectsInactive" 
}
$cbType = $mod.Find("LLMUnitySamples.ChatBot", $true)
$cbSig = New-Object dnlib.DotNet.ClassSig($cbType)
$spec = New-Object dnlib.DotNet.MethodSpecUser($mod.Import($findMethod), (New-Object dnlib.DotNet.GenericInstMethodSig($cbSig)))

# Component & Transform & GameObject methods
$compType = $coreMod.Find("UnityEngine.Component", $true)
$getTrans = $mod.Import($compType.FindMethod("get_transform"))
$getGo = $mod.Import($compType.FindMethod("get_gameObject"))

$transType = $coreMod.Find("UnityEngine.Transform", $true)
$getParent = $mod.Import($transType.FindMethod("get_parent"))

$goType = $coreMod.Find("UnityEngine.GameObject", $true)
$getActiveSelf = $mod.Import($goType.FindMethod("get_activeSelf"))
$setActive = $mod.Import($goType.FindMethod("SetActive"))

$opIneq = $mod.Import($objType.FindMethod("op_Inequality"))

# Add a local variable for Transform traversal
$transLocal = New-Object dnlib.DotNet.Emit.Local($mod.Import((New-Object dnlib.DotNet.ClassSig($transType))))
$procChat.Body.Variables.Add($transLocal)
$transLocalIdx = $procChat.Body.Variables.Count - 1

# Insert ldc.i4.1 before $ffIdx and change call operand to $spec
$procChat.Body.Instructions.Insert($ffIdx, [dnlib.DotNet.Emit.OpCodes]::Ldc_I4_1.ToInstruction())
$procChat.Body.Instructions[$ffIdx + 1].Operand = $spec

# Target position right after brfalse.s IL_0183
$activeInstIdx = $ffIdx + 5
$lblAfterActive = $procChat.Body.Instructions[$activeInstIdx]

$lblLoop = [dnlib.DotNet.Emit.OpCodes]::Ldloc.ToInstruction($transLocal)
$lblLoopDone = $lblAfterActive
$lblNext = [dnlib.DotNet.Emit.OpCodes]::Ldloc.ToInstruction($transLocal)

$actInsts = New-Object System.Collections.Generic.List[dnlib.DotNet.Emit.Instruction]

# Transform t = chatBot.transform
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc.ToInstruction($procChat.Body.Variables[4]))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getTrans))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Stloc.ToInstruction($transLocal))

# L_LOOP:
$actInsts.Add($lblLoop)
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldnull.ToInstruction())
$actInsts.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$opIneq))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brfalse.ToInstruction($lblLoopDone))

# if (!t.gameObject.activeSelf) t.gameObject.SetActive(true);
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc.ToInstruction($transLocal))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getGo))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getActiveSelf))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblNext))

$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldloc.ToInstruction($transLocal))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getGo))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Ldc_I4_1.ToInstruction())
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($setActive))

# L_NEXT: t = t.parent
$actInsts.Add($lblNext)
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getParent))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Stloc.ToInstruction($transLocal))
$actInsts.Add([dnlib.DotNet.Emit.OpCodes]::Br.ToInstruction($lblLoop))

for ($i = 0; $i -lt $actInsts.Count; $i++) {
    $procChat.Body.Instructions.Insert($activeInstIdx + $i, $actInsts[$i])
}

$procChat.Body.SimplifyBranches()
$procChat.Body.OptimizeBranches()
$procChat.Body.OptimizeMacros()
Write-Host "  ProcessChatMessage hierarchy activation inserted successfully."

Write-Host "Step 4: Hardening ChatBot.AllowInput against null inputBubble..."
$allowInput = $cbType.FindMethod("AllowInput")
# Currently:
# [0] ldarg.0
# [1] ldc.i4.0
# [2] stfld blockInput
# [3] ldarg.0
# [4] ldfld inputBubble
# [5] callvirt ReActivateInputField
# [6] ret
$inputBubbleField = $cbType.Fields | Where-Object { $_.Name -eq "inputBubble" }
$blockInputField = $cbType.Fields | Where-Object { $_.Name -eq "blockInput" }
$reActMethod = $allowInput.Body.Instructions[5].Operand

$allowInput.Body.Instructions.Clear()
$lblCallReAct = [dnlib.DotNet.Emit.OpCodes]::Ldarg_0.ToInstruction()
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ldarg_0.ToInstruction())
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ldc_I4_0.ToInstruction())
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Stfld.ToInstruction($blockInputField))
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ldarg_0.ToInstruction())
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($inputBubbleField))
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Brtrue.ToInstruction($lblCallReAct))
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ret.ToInstruction())
$allowInput.Body.Instructions.Add($lblCallReAct)
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($inputBubbleField))
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Callvirt, [dnlib.DotNet.IMethod]$reActMethod))
$allowInput.Body.Instructions.Add([dnlib.DotNet.Emit.OpCodes]::Ret.ToInstruction())
$allowInput.Body.SimplifyBranches()
$allowInput.Body.OptimizeBranches()
$allowInput.Body.OptimizeMacros()
Write-Host "  ChatBot.AllowInput null-check hardened successfully."

Write-Host "Step 5: Writing modified Assembly-CSharp.dll..."
$mod.Write($targetDll)
$mod.Dispose()
$coreMod.Dispose()

Write-Host "SUCCESS: Assembly-CSharp.dll cleanly patched and hardened!"
