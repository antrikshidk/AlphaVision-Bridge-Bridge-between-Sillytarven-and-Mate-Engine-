$ErrorActionPreference = "Stop"
Add-Type -Path "C:\DriveD\dnSpy-netframework\bin\dnlib.dll"

$managedDir = "C:\DriveD\MateEngineWin3.2 modded\MateEngineX_Data\Managed"
$dllPath = Join-Path $managedDir "Assembly-CSharp.dll"
$corePath = Join-Path $managedDir "UnityEngine.CoreModule.dll"

Write-Host "Step 1: Loading Assembly-CSharp.dll..."
$mod = [dnlib.DotNet.ModuleDefMD]::Load($dllPath)
$bridgeType = $mod.Find("MateEngineBridge", $true)
$bridgeType.Attributes = [dnlib.DotNet.TypeAttributes]"Public, BeforeFieldInit"
$bridgeType.FindMethod("ForceStandUp").Attributes = [dnlib.DotNet.MethodAttributes]"Public, HideBySig"
$chatReqType = $bridgeType.NestedTypes | Where-Object { $_.Name -eq "ChatRequest" }
$chatReqType.Attributes = [dnlib.DotNet.TypeAttributes]"NestedPublic, BeforeFieldInit"

# Add fields to ChatRequest if they don't already exist
$stringSig = $mod.CorLibTypes.String
$boolSig = $mod.CorLibTypes.Boolean

if (-not ($chatReqType.Fields | Where-Object { $_.Name -eq "message" })) {
    $chatReqType.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("message", (New-Object dnlib.DotNet.FieldSig($stringSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'message'"
}
if (-not ($chatReqType.Fields | Where-Object { $_.Name -eq "sender" })) {
    $chatReqType.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("sender", (New-Object dnlib.DotNet.FieldSig($stringSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'sender'"
}
if (-not ($chatReqType.Fields | Where-Object { $_.Name -eq "is_bubble" })) {
    $chatReqType.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("is_bubble", (New-Object dnlib.DotNet.FieldSig($boolSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'is_bubble'"
}
if (-not ($chatReqType.Fields | Where-Object { $_.Name -eq "add_to_chat" })) {
    $chatReqType.Fields.Add((New-Object dnlib.DotNet.FieldDefUser("add_to_chat", (New-Object dnlib.DotNet.FieldSig($boolSig)), [dnlib.DotNet.FieldAttributes]"Public")))
    Write-Host "  Added field 'add_to_chat'"
}

$tempDll = "C:\DriveD\mate_engine\MateEngineX_Data\alpha_env\temp_Assembly-CSharp.dll"
$mod.Write($tempDll)
$mod.Dispose()

Write-Host "Step 2: Compiling MateEngineChatHelper.cs against temp assembly..."
$helperCs = "C:\DriveD\mate_engine\MateEngineX_Data\alpha_env\MateEngineChatHelper.cs"
$helperDll = "C:\DriveD\mate_engine\MateEngineX_Data\alpha_env\MateEngineChatHelper.dll"

$csc = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /target:library /out:$helperDll /r:$corePath /r:$tempDll /nologo $helperCs
if ($LASTEXITCODE -ne 0) {
    throw "CSC compilation failed!"
}
Write-Host "  Compilation successful!"

Write-Host "Step 3: Merging MateEngineChatHelper into Assembly-CSharp.dll..."
$mod = [dnlib.DotNet.ModuleDefMD]::Load($tempDll)
$helperMod = [dnlib.DotNet.ModuleDefMD]::Load($helperDll)
$helperTypeSrc = $helperMod.Find("MateEngineChatHelper", $true)

# Import the helper type into Assembly-CSharp.dll
# We can create a new TypeDef in mod and copy methods, or copy the TypeDef
$importer = New-Object dnlib.DotNet.Importer($mod)
$helperTypeDef = New-Object dnlib.DotNet.TypeDefUser($helperTypeSrc.Namespace, $helperTypeSrc.Name, $mod.CorLibTypes.Object.TypeDefOrRef)
$helperTypeDef.Attributes = [dnlib.DotNet.TypeAttributes]"Public, Abstract, Sealed, BeforeFieldInit"
$mod.Types.Add($helperTypeDef)

foreach ($methodSrc in $helperTypeSrc.Methods) {
    if ($methodSrc.IsConstructor) { continue }
    $methodDef = New-Object dnlib.DotNet.MethodDefUser($methodSrc.Name, $importer.Import($methodSrc.MethodSig), $methodSrc.ImplAttributes, $methodSrc.Attributes)
    $methodDef.Body = New-Object dnlib.DotNet.Emit.CilBody
    $methodDef.Body.InitLocals = $methodSrc.Body.InitLocals
    $methodDef.Body.MaxStack = $methodSrc.Body.MaxStack
    foreach ($var in $methodSrc.Body.Variables) {
        $methodDef.Body.Variables.Add((New-Object dnlib.DotNet.Emit.Local($importer.Import($var.Type))))
    }
    foreach ($instr in $methodSrc.Body.Instructions) {
        $newOp = $instr.Operand
        if ($newOp -is [dnlib.DotNet.IMethod]) {
            $newOp = $importer.Import($newOp)
        } elseif ($newOp -is [dnlib.DotNet.IField]) {
            $newOp = $importer.Import($newOp)
        } elseif ($newOp -is [dnlib.DotNet.ITypeDefOrRef]) {
            $newOp = $importer.Import($newOp)
        }
        $newInstr = New-Object dnlib.DotNet.Emit.Instruction($instr.OpCode, $newOp)
        $methodDef.Body.Instructions.Add($newInstr)
    }
    # Fix branches
    for ($idx = 0; $idx -lt $methodSrc.Body.Instructions.Count; $idx++) {
        $srcInst = $methodSrc.Body.Instructions[$idx]
        if ($srcInst.Operand -is [dnlib.DotNet.Emit.Instruction]) {
            $targetIdx = $methodSrc.Body.Instructions.IndexOf($srcInst.Operand)
            $methodDef.Body.Instructions[$idx].Operand = $methodDef.Body.Instructions[$targetIdx]
        }
    }
    # Exception handlers
    foreach ($eh in $methodSrc.Body.ExceptionHandlers) {
        $newEh = New-Object dnlib.DotNet.Emit.ExceptionHandler($eh.HandlerType)
        if ($eh.CatchType -ne $null) {
            $newEh.CatchType = $importer.Import($eh.CatchType)
        }
        $newEh.TryStart = $methodDef.Body.Instructions[$methodSrc.Body.Instructions.IndexOf($eh.TryStart)]
        $newEh.TryEnd = $methodDef.Body.Instructions[$methodSrc.Body.Instructions.IndexOf($eh.TryEnd)]
        $newEh.HandlerStart = $methodDef.Body.Instructions[$methodSrc.Body.Instructions.IndexOf($eh.HandlerStart)]
        $newEh.HandlerEnd = $methodDef.Body.Instructions[$methodSrc.Body.Instructions.IndexOf($eh.HandlerEnd)]
        $methodDef.Body.ExceptionHandlers.Add($newEh)
    }
    $helperTypeDef.Methods.Add($methodDef)
    Write-Host "  Imported method: $($methodDef.Name)"
}

Write-Host "Step 4: Wiring NormalizeChatRequest into HandleChat..."
$bridgeType = $mod.Find("MateEngineBridge", $true)
$handleChat = $bridgeType.FindMethod("HandleChat")
$normMethodDef = $helperTypeDef.FindMethod("NormalizeChatRequest")

# In HandleChat:
# [07] call DeserializeObject
# [08] stfld data
# We want to insert:
# ldloc.0
# ldfld data
# call NormalizeChatRequest(data)
$insIdx = 9 # Right after [08] stfld data
$displayClassType = $bridgeType.NestedTypes | Where-Object { $_.Name -like "*DisplayClass42_0*" }
$dataField = $displayClassType.Fields | Where-Object { $_.Name -eq "data" }

$inst1 = [dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction()
$inst2 = [dnlib.DotNet.Emit.OpCodes]::Ldfld.ToInstruction($dataField)
$inst3 = [dnlib.DotNet.Emit.OpCodes]::Call.ToInstruction($normMethodDef)

$handleChat.Body.Instructions.Insert($insIdx, $inst1)
$handleChat.Body.Instructions.Insert($insIdx + 1, $inst2)
$handleChat.Body.Instructions.Insert($insIdx + 2, $inst3)
Write-Host "  Wired NormalizeChatRequest into HandleChat"

Write-Host "Step 5: Wiring EnsureChatBotResponse into ProcessChatMessage..."
$procChat = $bridgeType.FindMethod("ProcessChatMessage")
$ensureMethodDef = $helperTypeDef.FindMethod("EnsureChatBotResponse")

# Find the ShowBubble call instruction
$showBubbleInstIdx = -1
for ($i = 0; $i -lt $procChat.Body.Instructions.Count; $i++) {
    $inst = $procChat.Body.Instructions[$i]
    if ($inst.OpCode.Name -eq "call" -and $inst.Operand -ne $null -and $inst.Operand.ToString() -like "*ShowBubble*") {
        $showBubbleInstIdx = $i
        break
    }
}

if ($showBubbleInstIdx -lt 0) {
    throw "ShowBubble call not found in ProcessChatMessage!"
}
Write-Host "  Found ShowBubble at instruction index $showBubbleInstIdx"

# Right after ShowBubble(string), replace the old FindFirstObjectByType and DisplayAIResponse logic with EnsureChatBotResponse
# Find where instruction jumps to IL_0183 (TriggerLipSync)
# Let's locate the next section which is TriggerLipSync (starts with ldarg.3)
$triggerLipSyncIdx = -1
for ($i = $showBubbleInstIdx + 1; $i -lt $procChat.Body.Instructions.Count; $i++) {
    $inst = $procChat.Body.Instructions[$i]
    if ($inst.OpCode.Name -eq "ldarg.3") {
        $triggerLipSyncIdx = $i
        break
    }
}

Write-Host "  Replacing instructions between $showBubbleInstIdx and $triggerLipSyncIdx..."
# Remove instructions between showBubbleInstIdx + 1 and triggerLipSyncIdx
$removeCount = $triggerLipSyncIdx - ($showBubbleInstIdx + 1)
for ($r = 0; $r -lt $removeCount; $r++) {
    $procChat.Body.Instructions.RemoveAt($showBubbleInstIdx + 1)
}

# Insert:
# ldloc.0 (IntentAnalyzer result)
# callvirt IntentAnalyzer/AnalysisResult::get_CleanedText()
# call MateEngineChatHelper::EnsureChatBotResponse(string)
$intentType = $mod.Find("IntentAnalyzer", $true)
$intentResultType = $intentType.NestedTypes | Where-Object { $_.Name -eq "AnalysisResult" }
$getCleanedTextMethod = $intentResultType.FindMethod("get_CleanedText")

$ins1 = [dnlib.DotNet.Emit.OpCodes]::Ldloc_0.ToInstruction()
$ins2 = [dnlib.DotNet.Emit.OpCodes]::Callvirt.ToInstruction($getCleanedTextMethod)
$ins3 = [dnlib.DotNet.Emit.OpCodes]::Call.ToInstruction($ensureMethodDef)

$procChat.Body.Instructions.Insert($showBubbleInstIdx + 1, $ins1)
$procChat.Body.Instructions.Insert($showBubbleInstIdx + 2, $ins2)
$procChat.Body.Instructions.Insert($showBubbleInstIdx + 3, $ins3)

Write-Host "Step 6: Writing patched assembly to target location..."
$mod.Write($dllPath)
$mod.Dispose()
$helperMod.Dispose()

Write-Host "SUCCESS! Assembly-CSharp.dll patched and verified successfully."
