package cn.shenlun.companion

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.graphics.Canvas
import android.graphics.Color
import android.net.Uri
import android.util.Base64
import androidx.exifinterface.media.ExifInterface
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.UUID

object ImageWork {
    fun newPhoto(context: Context): File = File(File(context.filesDir,"photos").apply { mkdirs() },"${UUID.randomUUID()}.jpg")
    fun import(context: Context,uri: Uri): File {
        val output = newPhoto(context)
        try {
            context.contentResolver.openInputStream(uri).use { input ->
                requireNotNull(input) { "无法读取所选照片" }
                output.outputStream().use { out ->
                    val buffer=ByteArray(32768);var total=0
                    while(true) { val n=input.read(buffer);if(n<0)break;total+=n;require(total<=32*1024*1024) { "照片超过32MB，请选取较小的原图" };out.write(buffer,0,n) }
                }
            }
            val bounds=BitmapFactory.Options().apply { inJustDecodeBounds=true };BitmapFactory.decodeFile(output.path,bounds)
            require(bounds.outWidth>0 && bounds.outHeight>0 && bounds.outWidth.toLong()*bounds.outHeight<=200_000_000) { "照片格式或尺寸不受支持" }
            return output
        } catch(e:Exception) { output.delete();throw e }
    }
    fun bitmap(page: PhotoPage,maxEdge: Int): Bitmap {
        val options=BitmapFactory.Options().apply { inJustDecodeBounds=true };BitmapFactory.decodeFile(page.path,options)
        require(options.outWidth>0 && options.outHeight>0 && options.outWidth.toLong()*options.outHeight<=200_000_000) { "照片无法解码，请重新拍摄" }
        var sample=1
        while(maxOf(options.outWidth,options.outHeight)/sample>maxEdge*2)sample*=2
        val bitmap=BitmapFactory.decodeFile(page.path,BitmapFactory.Options().apply { inSampleSize=sample }) ?: error("照片无法读取")
        val orientation=runCatching { ExifInterface(page.path).getAttributeInt(ExifInterface.TAG_ORIENTATION,ExifInterface.ORIENTATION_NORMAL) }.getOrDefault(ExifInterface.ORIENTATION_NORMAL)
        val matrix=Matrix()
        when(orientation) {
            ExifInterface.ORIENTATION_FLIP_HORIZONTAL -> matrix.setScale(-1f,1f)
            ExifInterface.ORIENTATION_ROTATE_180 -> matrix.setRotate(180f)
            ExifInterface.ORIENTATION_FLIP_VERTICAL -> { matrix.setRotate(180f);matrix.postScale(-1f,1f) }
            ExifInterface.ORIENTATION_TRANSPOSE -> { matrix.setRotate(90f);matrix.postScale(-1f,1f) }
            ExifInterface.ORIENTATION_ROTATE_90 -> matrix.setRotate(90f)
            ExifInterface.ORIENTATION_TRANSVERSE -> { matrix.setRotate(-90f);matrix.postScale(-1f,1f) }
            ExifInterface.ORIENTATION_ROTATE_270 -> matrix.setRotate(-90f)
        }
        matrix.postRotate(page.rotation.toFloat())
        val scale=minOf(1f,maxEdge.toFloat()/maxOf(bitmap.width,bitmap.height));matrix.postScale(scale,scale)
        val result=Bitmap.createBitmap(bitmap,0,0,bitmap.width,bitmap.height,matrix,true)
        if(result!==bitmap)bitmap.recycle()
        return result
    }
    fun encode(page: PhotoPage): String {
        val bitmap=bitmap(page,2048)
        try {
            val stream=ByteArrayOutputStream()
            val opaque=if(bitmap.hasAlpha())Bitmap.createBitmap(bitmap.width,bitmap.height,Bitmap.Config.RGB_565).also { out -> Canvas(out).apply { drawColor(Color.WHITE);drawBitmap(bitmap,0f,0f,null) } } else bitmap
            try { opaque.compress(Bitmap.CompressFormat.JPEG,86,stream) } finally { if(opaque!==bitmap)opaque.recycle() }
            require(stream.size()<=3*1024*1024) { "照片压缩后仍过大，请裁小范围后重新拍摄" }
            return Base64.encodeToString(stream.toByteArray(),Base64.NO_WRAP)
        } finally { bitmap.recycle() }
    }
}
