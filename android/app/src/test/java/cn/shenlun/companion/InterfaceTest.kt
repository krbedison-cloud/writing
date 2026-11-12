package cn.shenlun.companion
import android.app.Application
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import androidx.compose.ui.graphics.asAndroidBitmap
import androidx.compose.ui.test.captureToImage
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.onRoot
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performScrollTo
import androidx.exifinterface.media.ExifInterface
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.File

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[35],qualifiers="w412dp-h915dp")
@GraphicsMode(GraphicsMode.Mode.NATIVE)
class InterfaceTest {
    @get:Rule val compose=createAndroidComposeRule<ComponentActivity>()
    private val app:Application get()=ApplicationProvider.getApplicationContext()
    private fun shot(name:String) {
        compose.waitForIdle()
        compose.runOnIdle {
            val view=compose.activity.window.decorView
            val bitmap=Bitmap.createBitmap(view.width,view.height,Bitmap.Config.ARGB_8888)
            view.draw(Canvas(bitmap))
            val dir=File("build/outputs/screenshots").apply { mkdirs() }
            File(dir,"$name.png").outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG,100,it) };bitmap.recycle()
        }
    }
    @Test fun modernHomeShowsPairingAndNativePaperFlow() {
        compose.setContent { CompanionTheme { PapersScreen(PhoneState(loading=false),{},{},{}) } }
        compose.onNodeWithText("连接我的电脑").assertIsDisplayed();compose.onNodeWithText("先和电脑打个招呼").assertIsDisplayed();shot("home")
    }
    @Test fun capturePageShowsQuestionPhotoActionsAndEditableText() {
        val q=Question("q",3,"essay","围绕基层治理创新写一篇文章，1000—1200字。","s")
        val state=PhoneState(loading=false,paper=Paper("p","2026 浙江省考 · A卷","2026","浙江","A卷",3),question=q)
        val vm=CompanionViewModel(app)
        compose.setContent { CompanionTheme { CaptureScreen(state,vm,{},{},{},{},{}) } }
        compose.onNodeWithText("答题纸照片").assertIsDisplayed();compose.onNodeWithText("拍一张").assertIsDisplayed();shot("capture")
    }
    @Test fun fullAppNavigatesToDedicatedOcrSettings() {
        val vm=CompanionViewModel(app)
        compose.setContent { CompanionTheme { CompanionApp(vm) } }
        compose.waitUntil(10000) { !vm.state.value.loading }
        compose.onNodeWithText("申论随拍").assertIsDisplayed();shot("app-home")
        compose.onNodeWithText("连接与 OCR").performClick()
        compose.onNodeWithText("OCR 专用 AI").performScrollTo().assertIsDisplayed();shot("app-settings")
    }
    @Test fun imageProcessingBoundsMemoryAndHonorsExifRotation() {
        val photo=ImageWork.newPhoto(app);val source=Bitmap.createBitmap(4000,3000,Bitmap.Config.ARGB_8888)
        val canvas=Canvas(source);canvas.drawColor(Color.WHITE);canvas.drawText("Handwritten answer test",100f,300f,Paint().apply { textSize=90f;color=Color.BLACK })
        photo.outputStream().use { source.compress(Bitmap.CompressFormat.JPEG,95,it) };source.recycle()
        ExifInterface(photo.path).apply { setAttribute(ExifInterface.TAG_ORIENTATION,ExifInterface.ORIENTATION_ROTATE_90.toString());saveAttributes() }
        val decoded=ImageWork.bitmap(PhotoPage(photo.path),2048);assertTrue(decoded.height>decoded.width);assertTrue(maxOf(decoded.width,decoded.height)<=2048);decoded.recycle()
        val rotated=ImageWork.bitmap(PhotoPage(photo.path,90),2048);assertTrue(rotated.width>rotated.height);rotated.recycle();photo.delete()
    }
}
