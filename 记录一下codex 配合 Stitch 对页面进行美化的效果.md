# 记录一下codex 配合 Stitch 对页面进行美化的效果
刚刚刷论坛接触到的stitch，也来展示下执行力了。 :face_savoring_food:

官网：
https://stitch.withgoogle.com/

## 原来的codex给我做的丑页面 :clown_face: 
![image|690x314](https://cdn3.linux.do/original/4X/7/1/7/7177e504653c1b23feea21ddf1dceaf5df3ff3c5.png)

## stitch给我设计的
![image|690x321](https://cdn3.linux.do/original/4X/6/8/9/68939e90c784d9cd6b6062aa82f92b1100f99130.jpeg)


## 添加stitch 到codex的mcp中
### config.toml 添加：
```
[mcp_servers.stitch]
url = "https://stitch.googleapis.com/mcp"
http_headers = { "X-Goog-Api-Key" = "your-api-key" }
# env_http_headers = { "X-Goog-Api-Key" = "STITCH_API_KEY" } ## 配合环境变量使用（安全考虑使用这种方式）
```
### api获取方式： 右上角点击自己头像 -> Stitch Settings -> 找到 API KEY -> Create Key 即可

### 打开 codex /mcp 查看
![image|690x297](https://cdn3.linux.do/original/4X/7/9/5/795e4ad123a62c1cbf8ed13395914616ae4671bc.png)

### 获取stitch项目
![image|690x151, 100%](https://cdn3.linux.do/original/4X/f/6/f/f6ffc586afe4e53b47234c7d76be49fdf6ed66fd.png)

### 最后一步指令：
先使用 Stitch 把xxx项目的设计系统（颜色/字体/圆角等）取出来，再对齐我本地项目的 UI 技术栈，把设计落实到代码里，最后做一轮可见的视觉升级并构建验证
![image|690x308](https://cdn3.linux.do/original/4X/c/a/5/ca5040b39200318600bcbad63828c1aba2fdcba2.png)

## "干"了多次的效果：
咱写后端的也不知道它说啥，干就完了 :clown_face:
![image|690x323](https://cdn3.linux.do/original/4X/0/2/b/02bca34c4653ca750314516c1ac3f2f0476c2fc8.png)

## 结论：
是好看了些，但是好像和设计图差距还是很大 :thinking:
初次试用，而且设计图和页面相差甚大，可以让Stitch根据我的网页来做设计，然后在通过codex+mcp来编码，效果应该是会好很多，有空再玩玩看 :face_savoring_food:

------
# 再次尝试结果：
https://linux.do/t/topic/1829424/34?u=leo-huang



