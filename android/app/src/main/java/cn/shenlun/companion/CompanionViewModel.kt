package cn.shenlun.companion

import android.app.Application
import android.net.Uri
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.util.UUID

data class PhoneState(
    val loading:Boolean=true,val server:ServerConfig=ServerConfig(),val ocr:OcrConfig=OcrConfig(),
    val papers:List<Paper> = emptyList(),val questions:List<Question> = emptyList(),val paper:Paper?=null,val question:Question?=null,
    val draft:AnswerDraft=AnswerDraft(),val busy:String?=null,val ocrRunning:Boolean=false,val message:String?=null,val status:String="",val error:String?=null
)

class CompanionViewModel(application:Application):AndroidViewModel(application) {
    private val store=SecureStore(application)
    private val mutable=MutableStateFlow(PhoneState())
    val state=mutable.asStateFlow()
    private val drafts=mutableMapOf<String,AnswerDraft>()
    private var saveJob:Job?=null
    private val saveMutex=Mutex()
    private var saveRevision=0L
    private var ocrJob:Job?=null
    private var initialized=false
    private fun key(server:ServerConfig=mutable.value.server,question:Question?=mutable.value.question)=(server.serverId.ifEmpty { server.url })+"|"+(question?.id ?: "")
    private fun saved()=SavedData(mutable.value.server,mutable.value.ocr,drafts.toMap(),mutable.value.paper,mutable.value.question)
    init {
        viewModelScope.launch {
            try {
                val saved=withContext(Dispatchers.IO) { store.read() }
                drafts.putAll(saved.drafts)
                mutable.value=PhoneState(loading=false,server=saved.server,ocr=saved.ocr,paper=saved.lastPaper,question=saved.lastQuestion,draft=drafts[key(saved.server,saved.lastQuestion)] ?: AnswerDraft())
                initialized=true
                if(saved.server.token.isNotEmpty())loadPapers()
            } catch(e:Exception) {
                initialized=true;mutable.value=mutable.value.copy(loading=false,error="本地加密配置暂时无法读取，原文件已保留。请检查系统密钥状态或重新配置。")
            }
        }
    }
    private fun persist(immediate:Boolean=false) {
        if(!initialized)return
        saveJob?.cancel();val snapshot=saved();val revision=++saveRevision
        saveJob=viewModelScope.launch {
            if(!immediate)delay(400)
            try { saveMutex.withLock { if(revision==saveRevision)withContext(Dispatchers.IO) { store.save(snapshot) } } } catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error="手机本地保存失败，请检查存储空间；当前文字未清空") }
        }
    }
    private fun draftChanged(value:AnswerDraft,target:String=key()) {
        drafts[target]=value
        if(target==key())mutable.value=mutable.value.copy(draft=value)
        persist()
    }
    fun editText(text:String) {
        if(text.length>20000)return
        draftChanged(mutable.value.draft.copy(text=text,requestId=""));clearMessage()
    }
    fun clearMessage(){mutable.value=mutable.value.copy(message=null,error=null)}
    fun clearError(){mutable.value=mutable.value.copy(error=null)}
    fun consumeMessage(expected:String){if(mutable.value.message==expected)mutable.value=mutable.value.copy(message=null)}
    fun showError(message:String){mutable.value=mutable.value.copy(error=message)}
    fun pair(address:String,code:String) {
        if(mutable.value.busy!=null)return
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在连接电脑",error=null)
            try {
                require(code.matches(Regex("\\d{6}"))) { "请输入电脑端显示的六位配对码" }
                val server=ServerApi.pair(address,code,"${Build.MANUFACTURER} ${Build.MODEL}")
                mutable.value=mutable.value.copy(server=server,paper=null,question=null,draft=AnswerDraft(),questions=emptyList(),message="已连接电脑，可以选择真题拍照录入",status="电脑已连接")
                persist(true);val papers=ServerApi(server).papers();mutable.value=mutable.value.copy(papers=papers)
            } catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=if(e is ApiFailure||e is IllegalArgumentException)e.message else "连接失败，请核对电脑地址和配对码") }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
    fun disconnect() {
        mutable.value=mutable.value.copy(server=ServerConfig(url=mutable.value.server.url),papers=emptyList(),questions=emptyList(),paper=null,question=null,draft=AnswerDraft(),status="已断开，原题草稿仍保存在手机")
        persist(true)
    }
    fun saveOcr(config:OcrConfig):Boolean {
        return try {
            mutable.value=mutable.value.copy(ocr=OcrApi.validate(config),message="OCR专用接口已加密保存在手机",error=null)
            persist(true);true
        } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message ?: "接口配置不完整");false }
    }
    fun loadPapers() {
        val server=mutable.value.server;if(server.token.isBlank())return
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在读取真题目录",error=null)
            try { val papers=ServerApi(server).papers();if(server==mutable.value.server)mutable.value=mutable.value.copy(papers=papers,status="已读取 ${papers.size} 套真题") }
            catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message ?: "真题目录暂时无法读取，已保存的草稿仍可使用") }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
    fun openPaper(paper:Paper) {
        val server=mutable.value.server
        mutable.value=mutable.value.copy(paper=paper,question=null,draft=AnswerDraft(),questions=emptyList())
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在读取题目",error=null)
            try {
                val questions=ServerApi(server).questions(paper.id)
                if(server==mutable.value.server)mutable.value=mutable.value.copy(paper=paper,questions=questions)
            } catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message ?: "暂时无法读取题目") }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
    fun selectQuestion(question:Question) {
        mutable.value=mutable.value.copy(question=question,draft=drafts[key(question=question)] ?: AnswerDraft(),status="",error=null)
        persist(true)
    }
    fun refreshQuestion() {
        val p=mutable.value.paper ?: return;val q=mutable.value.question ?: return;val server=mutable.value.server
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在刷新题目",error=null)
            try {
                val updated=ServerApi(server).questions(p.id).firstOrNull { it.id==q.id } ?: throw ApiFailure("电脑端已移除这道题，手机草稿仍保留")
                if(key(server,q)==key()) { mutable.value=mutable.value.copy(question=updated,status="题目已刷新，请核对后同步");persist(true) }
            } catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message) }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
    fun addPhoto(file:File,target:String=key()) {
        val draft=drafts[target] ?: if(target==key())mutable.value.draft else AnswerDraft()
        if(draft.photos.size>=6) { file.delete();mutable.value=mutable.value.copy(error="每次最多6页，请先识别或移除多余照片");return }
        draftChanged(draft.copy(photos=draft.photos+PhotoPage(file.path)),target)
    }
    fun importPhoto(uri:Uri) {
        val target=key()
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在导入照片",error=null)
            try { val file=withContext(Dispatchers.IO) { ImageWork.import(getApplication(),uri) };addPhoto(file,target) }
            catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message ?: "照片导入失败") }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
    fun draftKey()=key()
    fun rotate(index:Int) { val d=mutable.value.draft;draftChanged(d.copy(photos=d.photos.mapIndexed { i,p->if(i==index)p.copy(rotation=(p.rotation+90)%360) else p })) }
    fun move(index:Int,step:Int) { val d=mutable.value.draft;val target=index+step;if(target !in d.photos.indices)return;val photos=d.photos.toMutableList();val page=photos.removeAt(index);photos.add(target,page);draftChanged(d.copy(photos=photos)) }
    fun removePhoto(index:Int) { val d=mutable.value.draft;if(index !in d.photos.indices)return;val removed=d.photos[index];draftChanged(d.copy(photos=d.photos.filterIndexed { i,_->i!=index }));viewModelScope.launch(Dispatchers.IO) { File(removed.path).delete() } }
    fun recognize(replace:Boolean) {
        if(ocrJob?.isActive==true)return
        val initial=mutable.value.draft;val target=key();val config=mutable.value.ocr
        ocrJob=viewModelScope.launch {
            mutable.value=mutable.value.copy(ocrRunning=true,busy="正在处理照片",error=null)
            try {
                require(initial.photos.isNotEmpty()) { "请先拍照或从相册选择照片" }
                OcrApi.validate(config)
                val images=withContext(Dispatchers.Default) { initial.photos.map(ImageWork::encode) }
                mutable.value=mutable.value.copy(busy="AI正在识别 ${images.size} 页手写答案")
                val result=OcrApi.recognize(config,images)
                val current=drafts[target] ?: initial
                if(replace && current.text!=initial.text)throw ApiFailure("识别期间文字已修改，为保护草稿未自动覆盖。请确认后重新识别")
                val text=if(!replace&&current.text.isNotBlank())current.text+"\n"+result.text else result.text
                require(text.length<=20000) { "合并后文字超过20000字，请精简后重试" }
                draftChanged(current.copy(text=text,uncertain=result.uncertain,requestId=""),target)
                mutable.value=mutable.value.copy(status="识别完成，请核对□及易错字后同步电脑"+(result.outputTokens?.let { " · 输出 $it tokens" } ?: ""),message="识别结果已保存，原文用词未自动润色")
            } catch(e:CancellationException) { mutable.value=mutable.value.copy(status="识别已取消，照片和原文字仍保留");throw e }
            catch(e:Exception) { mutable.value=mutable.value.copy(error=if(e is ApiFailure||e is IllegalArgumentException)e.message else "识别失败，照片和原文字保留，可重试") }
            finally { mutable.value=mutable.value.copy(ocrRunning=false,busy=null) }
        }
    }
    fun cancelOcr(){ocrJob?.cancel()}
    fun sync() {
        if(mutable.value.busy!=null)return
        val p=mutable.value.paper ?: return;val q=mutable.value.question ?: return;val server=mutable.value.server
        val original=mutable.value.draft
        if(original.text.isBlank()) { mutable.value=mutable.value.copy(error="请先识别或输入文字");return }
        val draft=original.copy(requestId=original.requestId.ifEmpty { UUID.randomUUID().toString() });val target=key()
        draftChanged(draft,target)
        viewModelScope.launch {
            mutable.value=mutable.value.copy(busy="正在同步文字到电脑",error=null)
            try {
                // Persist the idempotency key before sending, including process-death recovery.
                saveJob?.cancel();++saveRevision
                val snapshot=saved()
                saveMutex.withLock { withContext(Dispatchers.IO) { store.save(snapshot) } }
                ServerApi(server).send(p,q,draft,mutable.value.ocr.model)
                val current=drafts[target] ?: draft
                draftChanged(current.copy(syncedText=draft.text),target)
                mutable.value=mutable.value.copy(status="已发送到电脑 · 打开本题练习页即可录入",message="同步成功，电脑端仍由你手动提交批改")
            } catch(e:CancellationException) { throw e } catch(e:Exception) { mutable.value=mutable.value.copy(error=e.message ?: "同步失败，文字仍保留，重试不会重复发送") }
            finally { mutable.value=mutable.value.copy(busy=null) }
        }
    }
}
