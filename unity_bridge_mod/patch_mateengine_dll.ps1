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

Write-Host "Step 5: Compiling and injecting AlphaSpontaneousToggle into SettingsHandlerToggles..."
$csPath = Join-Path $PSScriptRoot "AlphaSpontaneousToggle.cs"
if (-not (Test-Path $csPath)) { throw "Missing $csPath" }

$cscPath = "C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $cscPath)) { throw "Missing .NET 4 csc.exe at $cscPath" }

$tmpHelperDll = Join-Path $PSScriptRoot "AlphaSpontaneousToggle.tmp.dll"
$helperMod = $null
try {
    $refNames = @(
        "UnityEngine.CoreModule.dll",
        "UnityEngine.UI.dll",
        "UnityEngine.UIModule.dll",
        "Unity.TextMeshPro.dll",
        "UnityEngine.UnityWebRequestModule.dll"
    )
    $cscArgs = @("/nologo", "/nowarn:1684", "/target:library", "/optimize+", "/out:$tmpHelperDll")
    foreach ($rn in $refNames) {
        $cand = Join-Path $managedDir $rn
        if (-not (Test-Path $cand)) { throw "Required assembly reference not found: $rn" }
        $cscArgs += "/r:$cand"
    }
    $cscArgs += $csPath

    $cscOut = & $cscPath @cscArgs 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ("csc.exe compilation failed:`n" + ($cscOut -join "`n"))
    }

    $helperMod = [dnlib.DotNet.ModuleDefMD]::Load($tmpHelperDll)
    $srcToggleType = $helperMod.Find("AlphaSpontaneousToggle", $false)
    if ($null -eq $srcToggleType) { throw "AlphaSpontaneousToggle type not found in compiled helper DLL." }

    # Remove prior copy if ever run against an already-patched base
    $existingToggleType = $mod.Types | Where-Object { $_.FullName -eq "AlphaSpontaneousToggle" } | Select-Object -First 1
    if ($null -ne $existingToggleType) {
        [void]$mod.Types.Remove($existingToggleType)
    }

    $newToggleType = New-Object dnlib.DotNet.TypeDefUser("", "AlphaSpontaneousToggle", ($mod.Import($srcToggleType.BaseType)))
    $newToggleType.Attributes = $srcToggleType.Attributes
    $mod.Types.Add($newToggleType)

    function Clone-TypeSig {
        param([dnlib.DotNet.TypeSig]$sig)
        if ($null -eq $sig) { return $null }
        if ($sig -is [dnlib.DotNet.TypeDefOrRefSig]) {
            if ($sig.TypeDefOrRef -eq $srcToggleType) {
                return (New-Object dnlib.DotNet.ClassSig($newToggleType))
            }
            return (New-Object dnlib.DotNet.Importer($mod)).Import($sig)
        }
        if ($sig -is [dnlib.DotNet.SZArraySig]) {
            return (New-Object dnlib.DotNet.SZArraySig((Clone-TypeSig $sig.Next)))
        }
        if ($sig -is [dnlib.DotNet.ByRefSig]) {
            return (New-Object dnlib.DotNet.ByRefSig((Clone-TypeSig $sig.Next)))
        }
        if ($sig -is [dnlib.DotNet.PtrSig]) {
            return (New-Object dnlib.DotNet.PtrSig((Clone-TypeSig $sig.Next)))
        }
        if ($sig -is [dnlib.DotNet.PinnedSig]) {
            return (New-Object dnlib.DotNet.PinnedSig((Clone-TypeSig $sig.Next)))
        }
        if ($sig -is [dnlib.DotNet.GenericInstSig]) {
            $genClass = Clone-TypeSig $sig.GenericType
            $genArgs = New-Object 'System.Collections.Generic.List[dnlib.DotNet.TypeSig]'
            foreach ($ga in $sig.GenericArguments) {
                $genArgs.Add((Clone-TypeSig $ga))
            }
            return (New-Object dnlib.DotNet.GenericInstSig([dnlib.DotNet.ClassOrValueTypeSig]$genClass, $genArgs))
        }
        return (New-Object dnlib.DotNet.Importer($mod)).Import($sig)
    }

    function Import-Operand {
        param($op)
        if ($null -eq $op) { return $null }
        if ($op -is [dnlib.DotNet.IField] -and $fieldMap.ContainsKey($op)) {
            return $fieldMap[$op]
        }
        if ($op -is [dnlib.DotNet.IMethod] -and $methodMap.ContainsKey($op)) {
            return $methodMap[$op]
        }
        if ($op -is [dnlib.DotNet.ITypeDefOrRef] -and $op -eq $srcToggleType) {
            return $newToggleType
        }
        $imp = New-Object dnlib.DotNet.Importer($mod)
        if ($op -is [dnlib.DotNet.MethodSpec]) {
            $baseM = Import-Operand $op.Method
            $genArgs = New-Object 'System.Collections.Generic.List[dnlib.DotNet.TypeSig]'
            foreach ($ga in $op.GenericInstMethodSig.GenericArguments) {
                $genArgs.Add((Clone-TypeSig $ga))
            }
            $gimSig = New-Object dnlib.DotNet.GenericInstMethodSig($genArgs)
            return (New-Object dnlib.DotNet.MethodSpecUser([dnlib.DotNet.IMethodDefOrRef]$baseM, $gimSig))
        }
        if ($op -is [dnlib.DotNet.ITypeDefOrRef]) { return $imp.Import($op) }
        if ($op -is [dnlib.DotNet.IField]) { return $imp.Import($op) }
        if ($op -is [dnlib.DotNet.IMethod]) { return $imp.Import($op) }
        return $op
    }

    # Clone fields
    $fieldMap = @{}
    foreach ($f in $srcToggleType.Fields) {
        $fSig = New-Object dnlib.DotNet.FieldSig((Clone-TypeSig $f.FieldSig.Type))
        $nf = New-Object dnlib.DotNet.FieldDefUser($f.Name, $fSig, $f.Attributes)
        if ($f.HasConstant -and $null -ne $f.Constant) {
            $nf.Constant = New-Object dnlib.DotNet.ConstantUser($f.Constant.Value, $f.Constant.Type)
        }
        $newToggleType.Fields.Add($nf)
        $fieldMap[$f] = $nf
    }

    # Pre-create method definitions so intra-class calls resolve across methods
    $methodMap = @{}
    $methodPairs = @()
    foreach ($m in $srcToggleType.Methods) {
        $retSig = Clone-TypeSig $m.MethodSig.RetType
        $paramSigs = New-Object 'System.Collections.Generic.List[dnlib.DotNet.TypeSig]'
        foreach ($ps in $m.MethodSig.Params) {
            $paramSigs.Add((Clone-TypeSig $ps))
        }
        $mSig = New-Object dnlib.DotNet.MethodSig($m.MethodSig.CallingConvention, $m.MethodSig.GenParamCount, $retSig, $paramSigs)
        $nm = New-Object dnlib.DotNet.MethodDefUser($m.Name, $mSig, $m.ImplAttributes, $m.Attributes)
        foreach ($pd in $m.ParamDefs) {
            $nm.ParamDefs.Add((New-Object dnlib.DotNet.ParamDefUser($pd.Name, $pd.Sequence, $pd.Attributes)))
        }
        $newToggleType.Methods.Add($nm)
        $methodMap[$m] = $nm
        $methodPairs += ,@($m, $nm)
    }

    # Clone IL bodies
    foreach ($pair in $methodPairs) {
        $srcM = $pair[0]
        $dstM = $pair[1]
        if (-not $srcM.HasBody) { continue }

        $sb = $srcM.Body
        $db = New-Object dnlib.DotNet.Emit.CilBody
        $db.InitLocals = $sb.InitLocals

        $localMap = @{}
        foreach ($loc in $sb.Variables) {
            $nLoc = New-Object dnlib.DotNet.Emit.Local((Clone-TypeSig $loc.Type), $loc.Name)
            [void]$db.Variables.Add($nLoc)
            $localMap[$loc] = $nLoc
        }

        $paramMap = @{}
        for ($pi = 0; $pi -lt $srcM.Parameters.Count; $pi++) {
            $paramMap[$srcM.Parameters[$pi]] = $dstM.Parameters[$pi]
        }

        $instrMap = @{}
        $rawList = @()
        foreach ($ins in $sb.Instructions) {
            $op = $ins.Operand
            if ($op -is [dnlib.DotNet.Emit.Local]) {
                $newOp = $localMap[$op]
            } elseif ($op -is [dnlib.DotNet.Parameter]) {
                $newOp = $paramMap[$op]
            } elseif ($op -is [dnlib.DotNet.Emit.Instruction] -or $op -is [dnlib.DotNet.Emit.Instruction[]]) {
                $newOp = $null
            } else {
                $newOp = Import-Operand $op
            }
            $newIns = New-Object dnlib.DotNet.Emit.Instruction($ins.OpCode, $newOp)
            $instrMap[$ins] = $newIns
            $db.Instructions.Add($newIns)
            $rawList += ,@($ins, $newIns)
        }

        # Fix branch targets
        foreach ($ip in $rawList) {
            $oldOp = $ip[0].Operand
            if ($oldOp -is [dnlib.DotNet.Emit.Instruction]) {
                $ip[1].Operand = $instrMap[$oldOp]
            } elseif ($oldOp -is [dnlib.DotNet.Emit.Instruction[]]) {
                $targets = @()
                foreach ($t in $oldOp) { $targets += $instrMap[$t] }
                $ip[1].Operand = [dnlib.DotNet.Emit.Instruction[]]$targets
            }
        }

        # Clone exception handlers (try / catch / finally)
        foreach ($eh in $sb.ExceptionHandlers) {
            $neh = New-Object dnlib.DotNet.Emit.ExceptionHandler($eh.HandlerType)
            if ($null -ne $eh.TryStart) { $neh.TryStart = $instrMap[$eh.TryStart] }
            if ($null -ne $eh.TryEnd) { $neh.TryEnd = $instrMap[$eh.TryEnd] }
            if ($null -ne $eh.HandlerStart) { $neh.HandlerStart = $instrMap[$eh.HandlerStart] }
            if ($null -ne $eh.HandlerEnd) { $neh.HandlerEnd = $instrMap[$eh.HandlerEnd] }
            if ($null -ne $eh.FilterStart) { $neh.FilterStart = $instrMap[$eh.FilterStart] }
            if ($null -ne $eh.CatchType) { $neh.CatchType = [dnlib.DotNet.ITypeDefOrRef](Import-Operand $eh.CatchType) }
            $db.ExceptionHandlers.Add($neh)
        }

        $db.SimplifyBranches()
        $db.OptimizeBranches()
        $db.OptimizeMacros()
        $dstM.Body = $db
    }

    # Hook SettingsHandlerToggles.Start()
    $settingsToggles = $mod.Find("SettingsHandlerToggles", $true)
    if ($null -eq $settingsToggles) { throw "SettingsHandlerToggles type not found in Assembly-CSharp.dll" }
    $discordField = $settingsToggles.Fields | Where-Object { $_.Name -eq "enableDiscordRPCToggle" } | Select-Object -First 1
    if ($null -eq $discordField) { throw "SettingsHandlerToggles.enableDiscordRPCToggle field not found." }
    $settingsStart = $settingsToggles.FindMethod("Start")
    if ($null -eq $settingsStart -or -not $settingsStart.HasBody) { throw "SettingsHandlerToggles.Start() not found." }
    $attachMethod = $newToggleType.FindMethod("AttachBelow")
    if ($null -eq $attachMethod) { throw "AlphaSpontaneousToggle.AttachBelow not found after clone." }

    $sBody = $settingsStart.Body
    $sBody.SimplifyMacros($settingsStart.Parameters)
    $sBody.SimplifyBranches()
    $instrs = $sBody.Instructions
    $retIdx = -1
    for ($i = $instrs.Count - 1; $i -ge 0; $i--) {
        if ($instrs[$i].OpCode -eq [dnlib.DotNet.Emit.OpCodes]::Ret) {
            $retIdx = $i
            break
        }
    }
    if ($retIdx -lt 0) { throw "Could not find final ret in SettingsHandlerToggles.Start()" }

    # Mutate final ret in-place so existing branches jumping to ret run AttachBelow first
    $retIns = $instrs[$retIdx]
    $retIns.OpCode = [dnlib.DotNet.Emit.OpCodes]::Ldarg_0
    $retIns.Operand = $null
    $instrs.Insert($retIdx + 1, [dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Ldfld, $discordField))
    $instrs.Insert($retIdx + 2, [dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Call, [dnlib.DotNet.IMethod]$attachMethod))
    $instrs.Insert($retIdx + 3, [dnlib.DotNet.Emit.Instruction]::Create([dnlib.DotNet.Emit.OpCodes]::Ret))

    $sBody.OptimizeBranches()
    $sBody.OptimizeMacros()
    Write-Host "  AlphaSpontaneousToggle injected and hooked into SettingsHandlerToggles.Start()."
}
finally {
    if ($null -ne $helperMod) { $helperMod.Dispose() }
    if (Test-Path $tmpHelperDll) { Remove-Item $tmpHelperDll -Force -ErrorAction SilentlyContinue }
}

Write-Host "Step 6: Writing modified Assembly-CSharp.dll..."
$mod.Write($targetDll)
$mod.Dispose()
$coreMod.Dispose()

Write-Host "Step 7: Verifying written Assembly-CSharp.dll loads cleanly via dnlib..."
$verifyMod = [dnlib.DotNet.ModuleDefMD]::Load($targetDll)
try {
    $vt = $verifyMod.Find("AlphaSpontaneousToggle", $true)
    if ($null -eq $vt) { throw "Verification failed: AlphaSpontaneousToggle missing from written DLL." }
    $vs = $verifyMod.Find("SettingsHandlerToggles", $true).FindMethod("Start")
    $hasCall = $false
    foreach ($ins in $vs.Body.Instructions) {
        if ($ins.OpCode -eq [dnlib.DotNet.Emit.OpCodes]::Call -and $ins.Operand.ToString() -match "AlphaSpontaneousToggle::AttachBelow") {
            $hasCall = $true
            break
        }
    }
    if (-not $hasCall) { throw "Verification failed: AttachBelow call missing from SettingsHandlerToggles.Start()." }
    Write-Host ("  Verified: AlphaSpontaneousToggle ({0} methods, {1} fields) & SettingsHandlerToggles.Start hook OK." -f $vt.Methods.Count, $vt.Fields.Count)
}
finally {
    $verifyMod.Dispose()
}

Write-Host "SUCCESS: Assembly-CSharp.dll cleanly patched and hardened!"
