agentsdk
是一个底层的agent loop的sdk，python实现，可支持自定义agent loop，具体功能和使用方法请参考doc目录下的agentsdk 功能说明.md和agentsdk 开发者参考.md

agent_apps
基于agentsdk开发的agent应用，每个应用都会拷贝一个agentsdk实例，应用之间互不干扰。
每个应用的代码都在agent_apps目录下，每个应用的代码都在一个子目录下，子目录名就是应用的名称。


refs
参考的库，是一个typescript实现的agent sdk
