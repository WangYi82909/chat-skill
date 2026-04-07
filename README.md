<div align="center">

<img src="icon.png" alt="Project Icon" width="150" />

# 恋人skill

*对话基于海量聊天记录，每轮对话都会检索你们曾经有没有聊过相同的话题，模仿她的风格来跟你对话。*

<br>

> **“95%的回复在聊天记录中都有原句”**
>
> **“5%是你没有勇气说出来的话”**

</div>

---

## 项目介绍
提问：她/他不会和其他ai一样，你骂一句就立马生气，而是去检索，“她/他”是怎么解决的，原来“她”是先心平气和讲道理，我要尝试先引导一下用户，如果不听话，就重新构建人格，我要生气了！
- 1
**恋人skill** 
- 1.并非真正的skill，很多人不会使用claude，但是！！！请放心！本项目运行在python环境下！
- 2.必须使用聪明的模型，否则再强的提示词也没有用，看我的drive文件就知道了
务必保留core和drive两个文件，人格不是每次请求都注入，drive必须携带在userprompt中。
**重点！！！！**
- 1.上传最少万字聊天记录用于向量重排序和构建动态人格。
- 2.基于聊天记录蒸馏而成的超拟人化ai，每次回复先问问“聊天记录中有没有讨论过这个话题”，再去根据目前的人格进去回复。
- 3.其实我并没有夸大其词，你能聊的，百分之95%都是曾经聊过的话题
剩下的5%是你没有说出口的话。

## 快速开始

### 1. 克隆项目

确保你已安装 Python 3.8+，并安装必要的依赖：

```bash
git clone https://github.com/WangYi82909/chat-skill.git
```

### 2. 项目根目录安装依赖（不懂的问ai）

必须在根目录，可执行ls查看有没有此文件

```bash
pip3 install -r requirements.txt
```

### 3. 配置config.yaml文件

只需填写LLM配置，辅助llm配置，人物名称即可

### 4. 上传txt聊天记录并运行install文件
必须填写llm配置
聊天记录必须重命名放置在chat，格式无要求

```bash
python3 install.py
```
直接走完切片+向量前总结+插入数据库

### 5. 配置重排序
tools/query.py是本项目核心文件，检索聊天记录并重排序提升质量
进入后一眼就可以看见配置框。

#项目报错怎么办？
- 1.出现403，额度不足：404端点异常
本项目的install和main主程序共用config，但一个附带了/v1/chat/completions，一个没有
所有你肯定会遇到报错，要么加上/v1/chat/completions，要么删掉/v1/chat/completions。
- 2.在install时卡死，直接ctrl+c，重新运行。每一天基本上输入都是万token
- 3.重复调用工具换聪明模型

#给你们看看实际效果
<div align="center">
  <img src="img/mmexport1775601890590.jpg.png" alt="运行效果截图 1" width="300" />
  &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;
  <img src="img/img/mmexport1775601892646.png" alt="运行效果截图 2" width="300" />
</div>
