package ru.aicode.screening

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

data class Evidence(val line:Int,val text:String,val reason:String)
data class Finding(val file:String,val lines:String,val score:Int,val reason:String,val evidence:List<Evidence>)
data class Analysis(val repository:String,val filesAnalyzed:Int,val screeningSignal:Int,val findings:List<Finding>,val questions:List<String>)

class MainActivity:ComponentActivity(){
    override fun onCreate(savedInstanceState:Bundle?){super.onCreate(savedInstanceState);setContent{MaterialTheme{Surface(Modifier.fillMaxSize()){ScreeningApp()}}}}
    fun startAnalysis(repositoryUrl:String,onResult:(Result<Analysis>)->Unit){lifecycleScope.launch{val result=withContext(Dispatchers.IO){runCatching{
        val connection=(URL("http://10.0.2.2:8000/api/analyze").openConnection() as HttpURLConnection).apply{
            requestMethod="POST";connectTimeout=10000;readTimeout=60000;doOutput=true;setRequestProperty("Content-Type","application/json")}
        connection.outputStream.use{it.write(JSONObject().put("repository_url",repositoryUrl).toString().toByteArray())}
        if(connection.responseCode !in 200..299){val stream=connection.errorStream;val detail=stream?.bufferedReader()?.use{it.readText()}? : "";error("Backend: HTTP ${connection.responseCode} ${detail.take(240)}")}
        parseAnalysis(connection.inputStream.bufferedReader().use{it.readText()})}}
        onResult(result)}}}

    private fun parseAnalysis(body:String):Analysis{
        val root=JSONObject(body);val array=root.optJSONArray("findings")
        val findings=buildList{
            if(array!=null)for(i in 0 until array.length()){
                val item=array.getJSONObject(i);val ea=item.optJSONArray("evidence")
                val evidence=buildList{if(ea!=null)for(j in 0 until ea.length()){val e=ea.getJSONObject(j);add(Evidence(e.optInt("line"),e.optString("text"),e.optString("reason")))}}
                add(Finding(item.optString("file","Unknown file"),item.optString("lines","—"),item.optInt("score"),item.optString("reason"),evidence))}}
        val qa=root.optJSONArray("questions");val questions=buildList{if(qa!=null)for(i in 0 until qa.length())add(qa.getString(i))}
        return Analysis(root.optString("repository","GitHub repository"),root.optInt("files_analyzed"),root.optInt("screening_signal"),findings,questions)
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun ScreeningApp(){
    val activity=androidx.compose.ui.platform.LocalContext.current as? MainActivity
    var repository by remember{mutableStateOf("")};var loading by remember{mutableStateOf(false)};var error by remember{mutableStateOf<String?>(null)};var analysis by remember{mutableStateOf<Analysis?>(null)}
    Scaffold(topBar={TopAppBar(title={Column{Text("AI Code Screening",fontWeight=FontWeight.Bold);Text("Инструмент для HR",style=MaterialTheme.typography.labelSmall)}})}){padding->
        LazyColumn(Modifier.fillMaxSize().padding(padding).padding(horizontal=16.dp).navigationBarsPadding(),contentPadding=PaddingValues(top=4.dp,bottom=32.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
            item{HeroCard()}
            item{OutlinedTextField(value=repository,onValueChange={repository=it;error=null},modifier=Modifier.fillMaxWidth(),label={Text("GitHub repository")},placeholder={Text("https://github.com/owner/project")},supportingText={Text("Публичный репозиторий")},singleLine=true,shape=RoundedCornerShape(14.dp))}
            item{Button(onClick={val url=repository.trim();if(activity!=null){loading=true;error=null;analysis=null;activity.startAnalysis(url){r->loading=false;r.onSuccess{analysis=it}.onFailure{error=it.message?:"Неизвестная ошибка"}}}else error="Не удалось получить Android Activity"},enabled=repository.isNotBlank()&&!loading,modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(14.dp)){if(loading){CircularProgressIndicator(Modifier.size(20.dp),strokeWidth=2.dp);Spacer(Modifier.size(8.dp))};Text(if(loading)"Анализируем…" else "Начать анализ")}}
            if(analysis==null&&!loading)item{HowItWorksCard()}
            error?.let{item{Card(colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.errorContainer)){Text(it,Modifier.padding(16.dp),color=MaterialTheme.colorScheme.onErrorContainer)}}}
            analysis?.let{result->item{SummaryCard(result)};item{Text("Участки для проверки",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold)}
                if(result.findings.isEmpty())item{Card{Text("Модель не нашла локальных участков с достаточно сильными сигналами. Это не означает, что AI-кода нет.",Modifier.padding(16.dp))}}
                else items(result.findings){FindingCard(it)}
                item{Text("Вопросы для собеседования",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold)}
                items(result.questions){QuestionCard(it)}
                item{OutlinedButton(onClick={analysis=null;error=null},modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(14.dp)){Text("Проверить другой проект")}}
            }
        }
    }
}

@Composable private fun HeroCard(){Card(Modifier.fillMaxWidth(),shape=RoundedCornerShape(22.dp),colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.primaryContainer)){Column(Modifier.padding(22.dp)){Text("Проверка опыта разработчика",style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.Bold);Spacer(Modifier.height(8.dp));Text("Анализируйте публичные GitHub-проекты и получайте вопросы для технического скрининга.",style=MaterialTheme.typography.bodyLarge)}}}
@Composable private fun HowItWorksCard(){Card(Modifier.fillMaxWidth()){Column(Modifier.padding(18.dp)){Text("Как это работает",style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Bold);Spacer(Modifier.height(12.dp));Step("01","GitHub","Добавьте ссылку на публичный репозиторий");Step("02","Гибридный анализ","AST + структура + стиль + provenance");Step("03","Скрининг","Получите строки для проверки и вопросы кандидату");Spacer(Modifier.height(10.dp));HorizontalDivider();Spacer(Modifier.height(10.dp));Text("Сигнал не является вероятностью авторства и требует проверки человеком.",style=MaterialTheme.typography.bodySmall)}}}
@Composable private fun Step(number:String,title:String,description:String){Row(Modifier.fillMaxWidth().padding(vertical=5.dp),verticalAlignment=Alignment.CenterVertically){Box(Modifier.size(34.dp).background(MaterialTheme.colorScheme.secondaryContainer,RoundedCornerShape(10.dp)),contentAlignment=Alignment.Center){Text(number,fontWeight=FontWeight.Bold)};Spacer(Modifier.size(12.dp));Column{Text(title,fontWeight=FontWeight.SemiBold);Text(description,style=MaterialTheme.typography.bodySmall)}}}
@Composable private fun SummaryCard(result:Analysis){Card(Modifier.fillMaxWidth(),shape=RoundedCornerShape(20.dp)){Column(Modifier.padding(20.dp)){Text("Результат анализа",style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold);Text(result.repository);Spacer(Modifier.height(18.dp));Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){Metric("Сигнал","${result.screeningSignal}/100");Metric("Файлов",result.filesAnalyzed.toString());Metric("Участков",result.findings.size.toString())};Spacer(Modifier.height(14.dp));Text("Сигнал — агрегированный результат нескольких независимых признаков. Это не процент AI-кода и не доказательство использования нейросети.",style=MaterialTheme.typography.bodySmall)}}}
@Composable private fun Metric(label:String,value:String){Column{Text(value,style=MaterialTheme.typography.titleLarge,fontWeight=FontWeight.Bold);Text(label,style=MaterialTheme.typography.labelMedium)}}
@Composable private fun FindingCard(f:Finding){Card(Modifier.fillMaxWidth()){Column(Modifier.padding(16.dp)){Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween){Column(Modifier.weight(1f)){Text(f.file,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.SemiBold);Text("Строки: ${f.lines}",style=MaterialTheme.typography.labelMedium)};Text("${f.score}/100",fontWeight=FontWeight.Bold)};Spacer(Modifier.height(10.dp));Text(f.reason);if(f.evidence.isNotEmpty()){Spacer(Modifier.height(12.dp));Text("Почему участок отмечен",style=MaterialTheme.typography.titleSmall,fontWeight=FontWeight.Bold);f.evidence.forEach{EvidenceBlock(it);Spacer(Modifier.height(8.dp))}}}}}
@Composable private fun EvidenceBlock(e:Evidence){Card(colors=CardDefaults.cardColors(containerColor=MaterialTheme.colorScheme.secondaryContainer),shape=RoundedCornerShape(10.dp)){Column(Modifier.padding(10.dp)){Text("Строка ${e.line}",fontWeight=FontWeight.Bold);Text(e.text,fontFamily=FontFamily.Monospace,style=MaterialTheme.typography.bodySmall,modifier=Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surface,RoundedCornerShape(6.dp)).padding(8.dp));Text(e.reason,style=MaterialTheme.typography.bodySmall)}}}
@Composable private fun QuestionCard(q:String){Card(Modifier.fillMaxWidth()){Row(Modifier.padding(16.dp)){Text("?",fontWeight=FontWeight.Bold,style=MaterialTheme.typography.titleLarge);Spacer(Modifier.size(12.dp));Text(q,style=MaterialTheme.typography.bodyLarge)}}}
