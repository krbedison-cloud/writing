package cn.shenlun.companion

import android.content.Intent
import android.graphics.Bitmap
import android.os.Bundle
import android.os.SystemClock
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.core.content.FileProvider
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import java.io.File

class MainActivity:ComponentActivity() {
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState);enableEdgeToEdge()
        setContent { CompanionTheme { CompanionApp() } }
    }
}

@Composable fun CompanionTheme(content:@Composable ()->Unit) {
    val context=LocalContext.current;val dark=isSystemInDarkTheme()
    val colors=if(android.os.Build.VERSION.SDK_INT>=31) { if(dark)dynamicDarkColorScheme(context) else dynamicLightColorScheme(context) }
        else if(dark)darkColorScheme(primary=Color(0xFFABD5B6),secondary=Color(0xFFC6DAA5),surface=Color(0xFF101812))
        else lightColorScheme(primary=Color(0xFF2D6248),onPrimary=Color.White,primaryContainer=Color(0xFFD5ECD9),secondary=Color(0xFF576B3D),secondaryContainer=Color(0xFFE4ECCA),surface=Color(0xFFF7FAF4),surfaceContainer=Color(0xFFEBF1E8))
    MaterialTheme(colorScheme=colors,shapes=Shapes(small=RoundedCornerShape(12.dp),medium=RoundedCornerShape(20.dp),large=RoundedCornerShape(28.dp)),content=content)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable fun CompanionApp(vm:CompanionViewModel=viewModel()) {
    val state by vm.state.collectAsStateWithLifecycle();val context=LocalContext.current
    var screen by rememberSaveable { mutableStateOf("papers") };var settingsBack by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingPath by rememberSaveable { mutableStateOf<String?>(null) };var pendingTarget by rememberSaveable { mutableStateOf<String?>(null) }
    var previewPath by rememberSaveable { mutableStateOf<String?>(null) };var ocrChoice by remember { mutableStateOf(false) }
    val snackbar=remember { SnackbarHostState() }
    val camera=rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { success ->
        pendingPath?.let { path->val file=File(path);if(success&&file.length()>0)vm.addPhoto(file,pendingTarget ?: vm.draftKey()) else file.delete() }
        pendingPath=null;pendingTarget=null
    }
    val picker=rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri->if(uri!=null)vm.importPhoto(uri) }
    fun settings(){settingsBack=screen;screen="settings"}
    fun back(){screen=when(screen) { "capture"->if(state.questions.isEmpty())"papers" else "questions";"questions"->"papers";"settings"->settingsBack ?: "papers";else->"papers" };settingsBack=null}
    BackHandler(screen!="papers") { back() }
    LaunchedEffect(state.question,screen) { if(screen=="capture"&&state.question==null)screen="papers" }
    LaunchedEffect(state.message) { state.message?.let { message->snackbar.showSnackbar(message);vm.consumeMessage(message) } }
    Scaffold(
        topBar={ TopAppBar(title={ Column { Text(when(screen) { "capture"->"第 ${state.question?.number ?: ""} 题 · 手写录入";"questions"->"选择一道题";"settings"->"连接与 OCR";else->"申论随拍" },fontWeight=FontWeight.SemiBold);if(screen=="papers")Text("让纸上的每一笔，回到练习里",style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurfaceVariant) } },
            navigationIcon={ if(screen!="papers")IconButton(onClick={back()}) { Icon(Icons.Default.ArrowBack,"返回") } },
            actions={ if(screen=="papers")IconButton(onClick={vm.loadPapers()},enabled=state.server.token.isNotBlank()&&state.busy==null) { Icon(Icons.Default.Refresh,"刷新真题") };if(screen=="capture")IconButton(onClick={settings()}) { Icon(Icons.Default.Settings,"OCR接口设置") } }) },
        bottomBar={
            if(screen=="capture") Surface(tonalElevation=3.dp) {
                Column(Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(horizontal=20.dp,vertical=12.dp)) {
                    Button(onClick=vm::sync,enabled=state.server.token.isNotBlank()&&state.draft.text.isNotBlank()&&state.busy==null&&state.draft.text!=state.draft.syncedText,modifier=Modifier.fillMaxWidth().height(56.dp)) {
                        Icon(if(state.draft.text==state.draft.syncedText)Icons.Default.CheckCircle else Icons.Default.Send,null);Spacer(Modifier.width(10.dp));Text(if(state.draft.text==state.draft.syncedText)"已同步到电脑" else "校对完成，同步到电脑",style=MaterialTheme.typography.titleMedium)
                    }
                    Text("仅录入草稿 · 电脑端手动提交批改",modifier=Modifier.align(Alignment.CenterHorizontally).padding(top=7.dp),style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
                }
            } else if(screen=="papers"||screen=="settings") NavigationBar {
                NavigationBarItem(selected=screen=="papers",onClick={screen="papers";settingsBack=null},icon={Icon(Icons.Default.MenuBook,null)},label={Text("真题录入")})
                NavigationBarItem(selected=screen=="settings",onClick={screen="settings";settingsBack=null},icon={Icon(Icons.Default.Tune,null)},label={Text("连接与 OCR")})
            }
        },snackbarHost={SnackbarHost(snackbar)}
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) {
            if(state.loading)CircularProgressIndicator(Modifier.align(Alignment.Center))
            else when(screen) {
                "settings"->SettingsScreen(state,vm)
                "questions"->QuestionsScreen(state) { q->vm.selectQuestion(q);screen="capture" }
                "capture"->CaptureScreen(state,vm,onCamera={
                    try {
                        val file=ImageWork.newPhoto(context);pendingPath=file.path;pendingTarget=vm.draftKey()
                        camera.launch(FileProvider.getUriForFile(context,context.packageName+".photos",file))
                    } catch(e:Exception) { pendingPath?.let { File(it).delete() };pendingPath=null;vm.showError("无法打开系统相机，请检查是否安装并启用相机应用") }
                },onPick={picker.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly))},onPreview={previewPath=it},onSettings={settings()},onOcr={if(state.draft.text.isNotBlank())ocrChoice=true else vm.recognize(true)})
                else->PapersScreen(state,onSettings={settings()},onPaper={vm.openPaper(it);screen="questions"},onResume={screen="capture"})
            }
        }
    }
    if(state.error!=null)AlertDialog(onDismissRequest=vm::clearError,title={Text("这一步暂时没有完成")},text={Text(state.error!!)},confirmButton={TextButton(onClick=vm::clearError){Text("知道了")}})
    if(ocrChoice)AlertDialog(onDismissRequest={ocrChoice=false},title={Text("识别结果如何放入草稿？")},text={Text("当前已经有文字。识别失败或取消时，原文字都会保留。")},confirmButton={TextButton(onClick={ocrChoice=false;vm.recognize(false)}){Text("追加到末尾")}},dismissButton={TextButton(onClick={ocrChoice=false;vm.recognize(true)}){Text("识别成功后替换")}})
    val previewIndex=state.draft.photos.indexOfFirst { it.path==previewPath }
    if(previewIndex>=0)PhotoPreview(state.draft.photos[previewIndex],previewIndex,onRotate={vm.rotate(previewIndex)},onClose={previewPath=null})
}

@Composable private fun SectionCard(modifier:Modifier=Modifier,content:@Composable ColumnScope.()->Unit) {
    Surface(modifier.fillMaxWidth(),shape=MaterialTheme.shapes.large,color=MaterialTheme.colorScheme.surfaceContainerLow) { Column(Modifier.padding(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp),content=content) }
}
@Composable private fun EmptyPanel(icon:@Composable ()->Unit,title:String,detail:String,action:@Composable (() -> Unit)?=null) {
    Column(Modifier.fillMaxWidth().padding(vertical=36.dp,horizontal=24.dp),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(14.dp)) {
        Surface(shape=RoundedCornerShape(26.dp),color=MaterialTheme.colorScheme.secondaryContainer) { Box(Modifier.size(84.dp),contentAlignment=Alignment.Center) { icon() } }
        Text(title,style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold);Text(detail,style=MaterialTheme.typography.bodyLarge,color=MaterialTheme.colorScheme.onSurfaceVariant)
        action?.invoke()
    }
}
@Composable private fun BusyCard(state:PhoneState,vm:CompanionViewModel?=null) {
    if(state.busy!=null) {
        var elapsed by remember(state.busy) { mutableLongStateOf(0L) }
        LaunchedEffect(state.busy) { val start=SystemClock.elapsedRealtime();while(true){elapsed=(SystemClock.elapsedRealtime()-start)/1000;delay(1000)} }
        SectionCard { LinearProgressIndicator(Modifier.fillMaxWidth());Text(state.busy,style=MaterialTheme.typography.titleSmall);Text("已等待 ${elapsed} 秒 · 保留当前界面直到完成",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant);if(state.ocrRunning&&vm!=null)TextButton(onClick=vm::cancelOcr){Text("取消识别")}}
    }
}

@Composable internal fun PapersScreen(state:PhoneState,onSettings:()->Unit,onPaper:(Paper)->Unit,onResume:()->Unit) {
    var query by rememberSaveable { mutableStateOf("") }
    val filtered=remember(state.papers,query) { state.papers.filter { query.isBlank() || (it.title+it.year+it.province+it.volume).contains(query,true) } }
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
        item { Surface(shape=RoundedCornerShape(32.dp),color=MaterialTheme.colorScheme.primaryContainer) {
            Column(Modifier.padding(24.dp),verticalArrangement=Arrangement.spacedBy(14.dp)) {
                Row(verticalAlignment=Alignment.CenterVertically) { Icon(Icons.Default.DocumentScanner,null,Modifier.size(34.dp));Spacer(Modifier.width(12.dp));Text("写在纸上\n轻松回到练习里",style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.SemiBold) }
                Text("选一道题，拍下手写答案。AI 转成文字，你校对后再同步到电脑。",style=MaterialTheme.typography.bodyLarge)
                AssistChip(onClick=onSettings,label={Text(if(state.server.token.isEmpty())"尚未连接电脑" else "电脑已配对 · 局域网同步")},leadingIcon={Icon(if(state.server.token.isEmpty())Icons.Default.LinkOff else Icons.Default.Devices,null,Modifier.size(18.dp))})
            }
        } }
        if(state.server.token.isEmpty())item { EmptyPanel({Icon(Icons.Default.Phonelink,null,Modifier.size(36.dp))},"先和电脑打个招呼","打开电脑端的“手机拍照录入”，生成配对码。",{Button(onClick=onSettings){Text("连接我的电脑")}}) }
        if(state.paper!=null&&state.question!=null)item { FilledTonalButton(onClick=onResume,modifier=Modifier.fillMaxWidth(),contentPadding=PaddingValues(18.dp)) { Icon(Icons.Default.EditNote,null);Spacer(Modifier.width(10.dp));Text("继续第 ${state.question.number} 题的录入草稿") } }
        if(state.server.token.isNotEmpty()) {
            item { OutlinedTextField(query,{query=it},label={Text("搜索年份、省份或试卷")},leadingIcon={Icon(Icons.Default.Search,null)},singleLine=true,modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(24.dp)) }
            item { BusyCard(state) }
            if(filtered.isEmpty()&&state.busy==null)item { EmptyPanel({Icon(Icons.Default.MenuBook,null,Modifier.size(36.dp))},if(query.isBlank())"真题库还是空的" else "没有找到这套试卷",if(query.isBlank())"先在电脑端上传真题 PDF，再刷新这里。" else "换一个关键词试试。") }
            items(filtered,key={it.id}) { paper -> ElevatedCard(onClick={onPaper(paper)},modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(24.dp)) {
                Row(Modifier.fillMaxWidth().padding(20.dp),verticalAlignment=Alignment.CenterVertically) {
                    Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(8.dp)) { Text(paper.title,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.SemiBold,maxLines=2,overflow=TextOverflow.Ellipsis);Text("${paper.year} · ${paper.province} · ${paper.volume}  /  ${paper.questionCount}题",style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant) }
                    Icon(Icons.Default.ChevronRight,null,Modifier.padding(start=12.dp))
                }
            } }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable private fun QuestionsScreen(state:PhoneState,onQuestion:(Question)->Unit) {
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(16.dp)) {
        item { Text(state.paper?.title ?: "读取题目中",style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.SemiBold) }
        item { BusyCard(state) }
        items(state.questions,key={it.id}) { question -> OutlinedCard(onClick={onQuestion(question)},shape=RoundedCornerShape(28.dp)) {
            Column(Modifier.padding(20.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) {
                Row(verticalAlignment=Alignment.CenterVertically) { Surface(shape=RoundedCornerShape(16.dp),color=MaterialTheme.colorScheme.secondaryContainer) { Text(question.number.toString().padStart(2,'0'),Modifier.padding(14.dp),style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold) };Spacer(Modifier.width(14.dp));Text(kindLabel(question.kind),style=MaterialTheme.typography.titleSmall) }
                Text(question.prompt,style=MaterialTheme.typography.bodyLarge,maxLines=4,overflow=TextOverflow.Ellipsis)
                Row(Modifier.align(Alignment.End),verticalAlignment=Alignment.CenterVertically) { Text("拍照录入",color=MaterialTheme.colorScheme.primary,fontWeight=FontWeight.SemiBold);Icon(Icons.Default.ArrowForward,null,Modifier.padding(start=8.dp)) }
            }
        } }
    }
}

@Composable internal fun CaptureScreen(state:PhoneState,vm:CompanionViewModel,onCamera:()->Unit,onPick:()->Unit,onPreview:(String)->Unit,onSettings:()->Unit,onOcr:()->Unit) {
    val question=state.question
    var expanded by rememberSaveable(question?.id) { mutableStateOf(false) };var removeIndex by remember { mutableIntStateOf(-1) }
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(18.dp)) {
        item { SectionCard {
            Text(state.paper?.title ?: "本题录入",style=MaterialTheme.typography.labelLarge,color=MaterialTheme.colorScheme.primary)
            Text(question?.prompt ?: "请选择题目",style=MaterialTheme.typography.bodyLarge,maxLines=if(expanded)Int.MAX_VALUE else 4,overflow=TextOverflow.Ellipsis)
            TextButton(onClick={expanded=!expanded},contentPadding=PaddingValues(0.dp)) { Text(if(expanded)"收起题干" else "查看完整题干") }
        } }
        item { Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) { Text("答题纸照片",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold,modifier=Modifier.weight(1f));Text("${state.draft.photos.size} / 6 页",color=MaterialTheme.colorScheme.onSurfaceVariant) } }
        if(state.draft.photos.isEmpty())item { Surface(shape=RoundedCornerShape(28.dp),color=MaterialTheme.colorScheme.secondaryContainer.copy(alpha=.55f)) {
            Column(Modifier.fillMaxWidth().padding(28.dp),horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(10.dp)) { Icon(Icons.Default.CameraAlt,null,Modifier.size(42.dp),tint=MaterialTheme.colorScheme.primary);Text("把完整答案拍清楚",style=MaterialTheme.typography.titleMedium);Text("正对稿纸、光线均匀，尽量避开阴影和反光。多页按顺序添加。",style=MaterialTheme.typography.bodyMedium) }
        } }
        if(state.draft.photos.isNotEmpty())item { LazyRow(horizontalArrangement=Arrangement.spacedBy(12.dp)) { itemsIndexed(state.draft.photos,key={_,p->p.path}) { index,page ->
            OutlinedCard(onClick={onPreview(page.path)},modifier=Modifier.width(184.dp),shape=RoundedCornerShape(24.dp)) {
                PhotoImage(page,Modifier.fillMaxWidth().height(180.dp),400)
                Text("第 ${index+1} 页 · 点击放大",Modifier.padding(horizontal=12.dp,vertical=8.dp),style=MaterialTheme.typography.labelLarge)
                Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceEvenly) {
                    IconButton(onClick={vm.move(index,-1)},enabled=index>0&&state.busy==null) { Icon(Icons.Default.KeyboardArrowLeft,"前移一页") }
                    IconButton(onClick={vm.rotate(index)},enabled=state.busy==null) { Icon(Icons.Default.RotateRight,"向右旋转照片") }
                    IconButton(onClick={removeIndex=index},enabled=state.busy==null) { Icon(Icons.Default.DeleteOutline,"移除照片") }
                }
            }
        } } }
        item { Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(12.dp)) {
            FilledTonalButton(onClick=onCamera,enabled=state.busy==null&&state.draft.photos.size<6,modifier=Modifier.weight(1f).height(52.dp)) { Icon(Icons.Default.CameraAlt,null);Spacer(Modifier.width(8.dp));Text(if(state.draft.photos.isEmpty())"拍一张" else "再拍一页") }
            OutlinedButton(onClick=onPick,enabled=state.busy==null&&state.draft.photos.size<6,modifier=Modifier.weight(1f).height(52.dp)) { Icon(Icons.Default.PhotoLibrary,null);Spacer(Modifier.width(8.dp));Text("从相册选") }
        } }
        item { if(state.ocr.model.isBlank())OutlinedButton(onClick=onSettings,modifier=Modifier.fillMaxWidth().height(54.dp)) { Icon(Icons.Default.Key,null);Spacer(Modifier.width(10.dp));Text("先配置 OCR 专用 AI 接口") }
            else Button(onClick=onOcr,enabled=state.busy==null&&state.draft.photos.isNotEmpty(),modifier=Modifier.fillMaxWidth().height(56.dp)) { Icon(Icons.Default.AutoAwesome,null);Spacer(Modifier.width(10.dp));Text("识别这 ${state.draft.photos.size} 页手写答案") }
        }
        item { BusyCard(state,vm) }
        if(state.draft.uncertain.isNotEmpty())item { SectionCard { Text("这些位置需要多看一眼",style=MaterialTheme.typography.titleMedium,color=MaterialTheme.colorScheme.primary);state.draft.uncertain.forEach { Text("· $it",style=MaterialTheme.typography.bodyMedium) } } }
        item { Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) { Text("校对文字",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold,modifier=Modifier.weight(1f));Text("${state.draft.text.codePointCount(0,state.draft.text.length)} 字符",style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant) } }
        item { OutlinedTextField(state.draft.text,vm::editText,enabled=state.busy==null||state.ocrRunning,modifier=Modifier.fillMaxWidth(),minLines=9,maxLines=18,label={Text("识别结果可直接修改")},placeholder={Text("只转录你写下的答案，不自动润色或批改。难辨字用 □ 标记。")},shape=RoundedCornerShape(24.dp)) }
        if(state.status.isNotBlank())item { Text(state.status,style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.primary) }
        item { Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,verticalAlignment=Alignment.CenterVertically) { Text("文字和照片按题保存在手机",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant);TextButton(onClick=vm::refreshQuestion,enabled=state.busy==null){Text("刷新题目")}} }
        item { Spacer(Modifier.height(12.dp)) }
    }
    if(removeIndex>=0)AlertDialog(onDismissRequest={removeIndex=-1},title={Text("移除这张照片？")},text={Text("已识别的文字会保留。相册中的原照片不受影响。")},confirmButton={TextButton(onClick={vm.removePhoto(removeIndex);removeIndex=-1}){Text("移除")}},dismissButton={TextButton(onClick={removeIndex=-1}){Text("保留")}})
}

@Composable private fun PhotoImage(page:PhotoPage,modifier:Modifier,maxEdge:Int) {
    val bitmap by produceState<Bitmap?>(null,page.path,page.rotation,maxEdge) { value=withContext(Dispatchers.IO) { runCatching { ImageWork.bitmap(page,maxEdge) }.getOrNull() } }
    Box(modifier.background(MaterialTheme.colorScheme.surfaceContainerHighest),contentAlignment=Alignment.Center) {
        bitmap?.let { Image(it.asImageBitmap(),"答题纸照片",Modifier.fillMaxSize(),contentScale=ContentScale.Fit) } ?: CircularProgressIndicator(Modifier.size(24.dp))
    }
}
@Composable private fun PhotoPreview(page:PhotoPage,index:Int,onRotate:()->Unit,onClose:()->Unit) {
    Dialog(onDismissRequest=onClose,properties=DialogProperties(usePlatformDefaultWidth=false)) {
        Surface(Modifier.fillMaxSize(),color=MaterialTheme.colorScheme.surface) {
            Column(Modifier.fillMaxSize().safeDrawingPadding().padding(16.dp)) {
                Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) { Text("第 ${index+1} 页",style=MaterialTheme.typography.titleLarge,modifier=Modifier.weight(1f));IconButton(onClick=onClose){Icon(Icons.Default.Close,"关闭预览")} }
                PhotoImage(page,Modifier.fillMaxWidth().weight(1f),1280)
                FilledTonalButton(onClick=onRotate,modifier=Modifier.fillMaxWidth().padding(top=12.dp)) { Icon(Icons.Default.RotateRight,null);Spacer(Modifier.width(8.dp));Text("向右旋转") }
            }
        }
    }
}

@Composable internal fun SettingsScreen(state:PhoneState,vm:CompanionViewModel) {
    val context=LocalContext.current
    var address by rememberSaveable(state.server.url) { mutableStateOf(state.server.url) };var code by rememberSaveable { mutableStateOf("") }
    var protocol by rememberSaveable { mutableStateOf(state.ocr.protocol) };var url by rememberSaveable { mutableStateOf(state.ocr.url) };var model by rememberSaveable { mutableStateOf(state.ocr.model) }
    var key by remember { mutableStateOf(state.ocr.key) };var visible by remember { mutableStateOf(false) };var tokens by rememberSaveable { mutableStateOf(state.ocr.maxTokens.toString()) };var timeout by rememberSaveable { mutableStateOf(state.ocr.timeout.toString()) }
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(20.dp),verticalArrangement=Arrangement.spacedBy(20.dp)) {
        item { SectionCard {
            Row(verticalAlignment=Alignment.CenterVertically) { Icon(Icons.Default.Devices,null,tint=MaterialTheme.colorScheme.primary);Spacer(Modifier.width(10.dp));Text("我的电脑",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold) }
            Text("与 Mac 连接同一局域网。在电脑端“手机拍照录入”中生成配对码。",style=MaterialTheme.typography.bodyMedium)
            OutlinedTextField(address,{address=it},label={Text("电脑地址")},placeholder={Text("http://192.168.1.20:18086")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Uri),modifier=Modifier.fillMaxWidth())
            OutlinedTextField(code,{code=it.filter(Char::isDigit).take(6)},label={Text("六位配对码")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Number),modifier=Modifier.fillMaxWidth())
            Button(onClick={vm.pair(address,code)},enabled=state.busy==null,modifier=Modifier.fillMaxWidth().height(52.dp)){Text(if(state.server.token.isBlank())"连接电脑" else "重新配对")}
            if(state.server.token.isNotBlank())Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) { Icon(Icons.Default.CheckCircle,null,tint=MaterialTheme.colorScheme.primary);Text("已配对 · ${state.server.url}",Modifier.weight(1f).padding(horizontal=8.dp),style=MaterialTheme.typography.bodySmall);TextButton(onClick=vm::disconnect,enabled=state.busy==null){Text("断开")} }
        } }
        item { BusyCard(state,vm) }
        item { SectionCard {
            Row(verticalAlignment=Alignment.CenterVertically) { Icon(Icons.Default.AutoAwesome,null,tint=MaterialTheme.colorScheme.primary);Spacer(Modifier.width(10.dp));Text("OCR 专用 AI",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.SemiBold) }
            Text("独立于电脑批改接口。请使用支持图片输入的视觉模型，照片直接发送到你填写的地址。",style=MaterialTheme.typography.bodyMedium)
            Row(horizontalArrangement=Arrangement.spacedBy(6.dp)) { listOf("openai" to "OpenAI兼容","anthropic" to "Claude","gemini" to "Gemini").forEach { (id,label)->FilterChip(selected=protocol==id,onClick={val defaults=mapOf("openai" to "https://api.openai.com/v1","anthropic" to "https://api.anthropic.com/v1","gemini" to "https://generativelanguage.googleapis.com/v1beta");if(url.trimEnd('/') in defaults.values&&protocol!=id){url=defaults.getValue(id);key=""};protocol=id},label={Text(label)}) } }
            OutlinedTextField(url,{url=it},label={Text("API 根地址")},placeholder={Text("https://服务商地址/v1")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Uri),modifier=Modifier.fillMaxWidth())
            OutlinedTextField(model,{model=it},label={Text("视觉模型 ID")},singleLine=true,modifier=Modifier.fillMaxWidth())
            OutlinedTextField(key,{key=it},label={Text("API Key")},singleLine=true,visualTransformation=if(visible)VisualTransformation.None else PasswordVisualTransformation(),trailingIcon={IconButton(onClick={visible=!visible}){Icon(if(visible)Icons.Default.VisibilityOff else Icons.Default.Visibility,if(visible)"隐藏密钥" else "显示密钥")}},modifier=Modifier.fillMaxWidth())
            Row(horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                OutlinedTextField(tokens,{tokens=it.filter(Char::isDigit)},label={Text("输出 token 上限")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Number),modifier=Modifier.weight(1f))
                OutlinedTextField(timeout,{timeout=it.filter(Char::isDigit)},label={Text("等待时限（秒）")},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Number),modifier=Modifier.weight(1f))
            }
            Button(onClick={vm.saveOcr(OcrConfig(protocol,url,model,key,tokens.toIntOrNull() ?: 0,timeout.toIntOrNull() ?: 0))},modifier=Modifier.fillMaxWidth().height(52.dp)){Icon(Icons.Default.Save,null);Spacer(Modifier.width(8.dp));Text("保存 OCR 接口")}
            Text("密钥在手机内加密保存。默认8192输出 tokens、等待600秒；识别失败不会清空照片或原文字。",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        } }
        item { SectionCard { Text("轻一点，也认真一点",style=MaterialTheme.typography.titleMedium);Text("原生界面 · 系统相机与照片选择器 · 图片适度压缩后识别。只负责手写答案录入，不在手机运行大模型。",style=MaterialTheme.typography.bodyMedium);Text("申论随拍 ${BuildConfig.VERSION_NAME} · Android 10+",style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurfaceVariant);TextButton(onClick={context.startActivity(Intent(Intent.ACTION_VIEW,android.net.Uri.parse("https://github.com/krbedison-cloud/writing/releases")))}){Text("查看更新")}} }
    }
}
