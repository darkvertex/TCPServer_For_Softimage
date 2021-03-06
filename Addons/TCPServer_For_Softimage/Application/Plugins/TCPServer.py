"""
**TCPServer.py**

**Platform:**
	Windows, Linux.

**Description:**
	| This module defines the :class:`TCPServer`class and other helpers objects needed to run a **Python** socket server
	inside **Autodesk Softimage** in a similar way than **Autodesk Maya** command port.
	| This module has been created as a replacement to
	`sIBL_GUI_XSI_Server <https://github.com/KelSolaar/sIBL_GUI_XSI_Server>`_ addon for 2 major reasons:

		- The fact that **sIBL_GUI_XSI_Server** was a C# addon needing to be recompiled for each **Autodesk Softimage**
version.
		- The need for a generic socket server that could be easily extended and modified because
it's written in **Python**.

	| Some examples exists, especially on `XSI-Blog <http://www.softimageblog.com/archives/132>`_
	unfortunately they don't work anymore with current **Autodesk Softimage** releases,
	resulting in application getting blocked while the code is executed.
	| To prevent this the :class:`TCPServer`class code is executed in a separate thread using the
	:mod:`SocketServer`.
	| One of the major issue encountered while implementing the server was because the client code was getting executed
	into the server thread resulting in random application crashes.
	| The trick to avoid this has been to create a global requests stack using :class:`collections.deque` class shared
	between the main application thread and the server thread, then a timer event poll the data on a regular interval and
	process it.
	| Another issue was the scopes oddities happening within the code and especially inside the PPG logic. It seems that
	the PPG logic definitions are called in another scope than the module one, making it hard to access module objects and
	annoying if you don't want to expose everything in application commands.
	| Hopefully, thanks to **Python** introspection it's possible to retrieve the correct module object. For that,
	a global :data:`__uid__` attribute is defined, then the list of objects handled by the garbage collector is traversed
	until one with the attribute is found. See :def:`_getModule` definition for more details.
	| An alternate design using the plugin **UserData** attribute has been tested but never managed to wrap correcly
	the :class:`collections.deque` class inside a COM object.

**Usage:**

	| Download and install the addon like any other addon. It should be available in the plug-ins manager as
	**TCPServer_For_Softimage**.
	| The server should start automatically with **Autodesk Softimage** startup. You can also start it using the
	**TCPServer_start** command or the **TCPServer_property** available in the View -> TCPServer -> TCPServer Preferences
	menu.

**Handlers:**
	| Different handlers are available:
	| The :class:`EchoRequestsHandler` class that writes to standard output what the client send and echo it back:

	Example client code:

		>>> import socket
		>>> connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		>>> connection.connect(("127.0.0.1", 12288))
		>>> connection.send("Hello World!")
		12
		>>> connection.recv(1024)
		'Hello World!'
		>>> connection.close()

	The :class:`DefaultStackDataRequestsHandler` class handles two types of string formatting:

		- An existing script file path: "C://MyScript//PythonScript.py" in that case the script would be executed as
		a **Python** script by the application.

		- A string with the following formatting: "Language | Code", "JScript | LogMessage(\"Pouet!\")" in that case
		the given code would be executed as **Python** JScript by the application resulting in **Pouet!** being logged.

	Example client code:

		>>> import socket
		>>> connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		>>> connection.connect(("127.0.0.1", 12288))
		>>> connection.send("JScript | LogMessage(\"Pouet\")")
		29
		>>> connection.send("C:/Users/KelSolaar/AppData/Roaming/HDRLabs/sIBL_GUI/4.0/io/loaderScripts/sIBL_XSI_Import.js")
		91
		>>> connection.close()

	The :class:`LoggingStackDataRequestsHandler` class that verbose what the client send:

	Example client code:

		>>> import socket
		>>> connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		>>> connection.connect(("127.0.0.1", 12288))
		>>> connection.send("Hello World!")
		12
		>>> connection.close()

	The :class:`PythonStackDataRequestsHandler` class that will aggregate the data the client send until it encounters the
	:attr:`PythonStackDataRequestsHandler.requestEnd` attribute and then executes the given data as **Python** code.

	Example client code:

		>>> import socket
		>>> connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
		>>> connection.connect(("127.0.0.1", 12288))
		>>> connection.send("import sys\nprint sys.maxint<!RE>")
		33
		>>> connection.close()

**Others:**

"""

#**********************************************************************************************************************
#***	External imports.
#**********************************************************************************************************************
import SocketServer
import collections
import inspect
import os
import re
import socket
import threading
import time
import ConfigParser
from win32com.client import constants as siConstants

#**********************************************************************************************************************
#***	Module attributes.
#**********************************************************************************************************************
__author__ = "Thomas Mansencal"
__copyright__ = "Copyright (C) 2008 - 2013 - Thomas Mansencal"
__license__ = "GPL V3.0 - http://www.gnu.org/licenses/"
__maintainer__ = "Thomas Mansencal"
__email__ = "thomas.mansencal@gmail.com"
__status__ = "Production"

__uid__ = "ab7c34a670c7737f491edfd2939201c4"

__all__ = ["ProgrammingError",
			"AbstractServerError",
			"ServerOperationError",
			"EchoRequestsHandler",
			"LoggingStackDataRequestsHandler",
			"DefaultStackDataRequestsHandler",
			"PythonStackDataRequestsHandler",
			"Constants",
			"Runtime",
			"TCPServer",
			"XSILoadPlugin",
			"XSIUnloadPlugin"]

#**********************************************************************************************************************
#***	Module classes and definitions.
#**********************************************************************************************************************
class ProgrammingError(Exception):
	pass

class AbstractServerError(Exception):
	pass

class ServerOperationError(AbstractServerError):
	pass

class EchoRequestsHandler(SocketServer.BaseRequestHandler):

	def handle(self):
		while True:
			data = self.request.recv(1024)
			if not data:
				break

			self.request.send(data)
		return True

	@staticmethod
	def processData():
		pass

class LoggingStackDataRequestsHandler(SocketServer.BaseRequestHandler):

	def handle(self):
		while True:
			data = self.request.recv(1024)
			if not data:
				break

			Runtime.requestsStack.append(data)
		return True

	@staticmethod
	def processData():
		while Runtime.requestsStack:
			Application.LogMessage(Runtime.requestsStack.popleft())
		return True

class DefaultStackDataRequestsHandler(SocketServer.BaseRequestHandler):

	def handle(self):
		while True:
			data = self.request.recv(1024)
			if not data:
				break

			Runtime.requestsStack.append(data)
		return True

	@staticmethod
	def processData():
		while Runtime.requestsStack:
			data = Runtime.requestsStack.popleft().strip()
			if os.path.exists(data):
				value = Application.ExecuteScript(data)
				# Application.LogMessage("%s | Request return value: '%s'." % (Constants.name, value), siConstants.siVerbose)
			else:
				for language in Constants.languages:
					match = re.match(r"\s*(?P<language>%s)\s*\|(?P<code>.*)" % language, data)
					if match:
						value = Application.ExecuteScriptCode(match.group("code"), match.group("language"))
						Application.LogMessage("%s | Request return value: '%s'." % (Constants.name, value), siConstants.siVerbose)
						break
		return True

class PythonStackDataRequestsHandler(SocketServer.BaseRequestHandler):

	requestEnd = "<!RE>"

	def handle(self):
		allData = []
		while True:
			data = self.request.recv(1024)
			if not data:
				break

			if self.requestEnd in data:
				allData.append(data[:data.find(self.requestEnd)])
				break

			allData.append(data)
			if len(allData) >=1:
				tail = allData[-2] + allData[-1]
				if self.requestEnd in tail:
					allData[-2] = tail[:tail.find(self.requestEnd)]
					allData.pop()
					break

		Runtime.requestsStack.append("".join(allData))
		return True

	@staticmethod
	def processData():
		while Runtime.requestsStack:
			value = Application.ExecuteScriptCode(Runtime.requestsStack.popleft(), "Python")
			Application.LogMessage("%s | Request return value: '%s'." % (Constants.name, value), siConstants.siVerbose)
		return True

class Constants(object):

	name = "TCPServer"
	author = __author__
	email = __email__
	website = "http://www.thomasmansencal.com/"
	majorVersion = 1
	minorVersion = 0
	patchVersion = 0
	settings = "TCPServer_settings_property"
	logo = "pictures/TCPServer_Logo.bmp"
	defaultAddress = "127.0.0.1"
	defaultPort = 12288
	defaultRequestsHandler = DefaultStackDataRequestsHandler
	languages = ("VBScript", "JScript", "Python", "PythonScript", "PerlScript")

class Runtime(object):

	server = None
	address = Constants.defaultAddress
	port = Constants.defaultPort
	requestsHandler = Constants.defaultRequestsHandler
	requestsStack = collections.deque()

class TCPServer(object):

	def __init__(self, address, port, handler=EchoRequestsHandler):
		self.__address = None
		self.address = address
		self.__port = None
		self.port = port
		self.__handler = None
		self.handler = handler

		self.__server = None
		self.__worker = None
		self.__online = False

	#******************************************************************************************************************
	#***	Attributes properties.
	#******************************************************************************************************************

	def address_get(self):
		return self.__address

	def address_set(self, value):
		if value is not None:
			assert type(value) in (str, unicode), "'%s' attribute: '%s' type is not 'str' or 'unicode'!" % ("address", value)
		self.__address = value

	def address_delete(self):
		raise ProgrammingError("%s | '%s' attribute is not deletable!" % (self.__class__.__name__, "address"))

	address = property(address_get,address_set,address_delete)

	def port_get(self):
		return self.__port

	def port_set(self, value):
		if value is not None:
			assert type(value) is int, "'%s' attribute: '%s' type is not 'int'!" % ("port", value)
		self.__port = value

	def port_delete(self):
		raise ProgrammingError("%s | '%s' attribute is not deletable!" % (self.__class__.__name__, "port"))

	port = property(port_get,port_set,port_delete)

	def handler_get(self):
		return self.__handler

	def handler_set(self, value):
		if value is not None:
			assert issubclass(value, SocketServer.BaseRequestHandler), \
			"'%s' attribute: '%s' is not 'SocketServer.BaseRequestHandler' subclass!" % ("handler", value)
		self.__handler = value

	def handler_delete(self):
		raise ProgrammingError("%s | '%s' attribute is not deletable!" % (self.__class__.__name__, "handler"))

	handler = property(handler_get,handler_set,handler_delete)

	def online_get(self):
		return self.__online

	def online_set(self, value):
		raise ProgrammingError("%s | '%s' attribute is read only!" % (self.__class__.__name__, "online"))

	def online_delete(self):
		raise ProgrammingError("%s | '%s' attribute is not deletable!" % (self.__class__.__name__, "online"))

	online = property(online_get,online_set,online_delete)

	#******************************************************************************************************************
	#***	Class methods.
	#******************************************************************************************************************
	def start(self):
		if self.__online:
			raise ServerOperationError("%s | '%s' server is already online!" % (self.__class__.__name__, self))

		try:
			self.__server = SocketServer.TCPServer((self.__address, self.__port), self.__handler)
			self.__worker = threading.Thread(target=self.__server.serve_forever)
			self.__worker.setDaemon(True)
			self.__worker.start()
			self.__online = True
			Application.LogMessage(
			"%s | Server successfully started on '%s' address and '%s' port using '%s' requests handler!" % (self.__class__.__name__, self.__address, self.__port, self.__handler.__name__),
			siConstants.siInfo)
			return True
		except socket.error, ex:
			error_number, error_message = ex
			if error_number == 10048:
				Application.LogMessage(
				"%s | Cannot start server, a connection is already opened on port '%s'!" % (self.__class__.__name__, self, self.__port), siConstants.siWarning)
			else:
				raise socket.error

	def stop(self):
		if not self.__online:
			raise ServerOperationError("%s | '%s' server is not online!" % (self.__class__.__name__, self))

		self.__server.shutdown()
		self.__server = None
		self.__worker = None
		self.__online = False
		Application.LogMessage("%s | Server successfully stopped!" % (self.__class__.__name__), siConstants.siInfo)
		return True


def _getServerStatusFilePath():
	"""
	Returns the expected path to tcpserver.ini
	"""
	return XSIUtils.BuildPath( XSIUtils.Environment('TEMP'), 'tcpserver.ini')

def _setServerStatusFile(**kwargs):
	"""
	Writes the tcpserver.ini file, such as:

		[info]
		active=1
		handler=DefaultStackDataRequestsHandler
		address=127.0.0.1
		port=22778
		touched=<time>
		xsibooted=<time>
		started=<time>

	"""
	config = ConfigParser.ConfigParser()
	sect = 'info'
	config.add_section(sect)

	kwargs['active'] = kwargs.setdefault('active', 0)
	kwargs['touched'] = int(time.time())
	# kwargs['port'] = kwargs.setdefault('port', Constants.defaultPort)
	# kwargs['address'] = kwargs.setdefault('address', Constants.defaultAddress)
	# kwargs['handler'] = kwargs.setdefault('handler', Constants.defaultRequestsHandler)

	for key, val in kwargs.items():
		config.set(sect, key, val)

	configFile = open( _getServerStatusFilePath(), 'wb' )
	config.write(configFile)
	configFile.close()
	return True

def _getServerStatusFileData():
	"""
	Reads the server status .ini file as a dictionary.
	"""
	path = _getServerStatusFilePath()
	if not os.path.exists(path):
		return False

	config = ConfigParser.ConfigParser()
	config.read(path)
	section = 'info'
	data = dict([ (option, config.get(section, option)) for option in config.options(section) ])

	return data


def XSILoadPlugin(pluginRegistrar):
	pluginRegistrar.Author = Constants.author
	pluginRegistrar.Name = Constants.name
	pluginRegistrar.URL = Constants.website
	pluginRegistrar.Email = Constants.email
	pluginRegistrar.Major = Constants.majorVersion
	pluginRegistrar.Minor = Constants.minorVersion

	pluginRegistrar.RegisterEvent("TCPServer_startupEvent", siConstants.siOnStartup)
	pluginRegistrar.RegisterCommand("TCPServer_start", "TCPServer_start")
	pluginRegistrar.RegisterCommand("TCPServer_stop", "TCPServer_stop")
	pluginRegistrar.RegisterTimerEvent("TCPServer_timerEvent", 250, 0)
	pluginRegistrar.RegisterMenu(siConstants.siMenuMainApplicationViewsID, "TCPServer")

	pluginRegistrar.RegisterProperty("TCPServer_property");

	Application.LogMessage("'%s' has been loaded!" % pluginRegistrar.Name)
	return True

def XSIUnloadPlugin(pluginRegistrar):
	_stopServer()
	Application.LogMessage("'%s' has been unloaded!" % pluginRegistrar.Name)
	return True

def TCPServer_startupEvent_OnEvent(context):
	Application.LogMessage("%s | 'TCPServer_startupEvent_OnEvent' called!" % Constants.name, siConstants.siVerbose)
	_registerSettingsProperty()
	_restoreSettings()
	_setServerStatusFile( xsibooted=time.time() )
	_startServer()
	return True

def TCPServer_start_Init(context):
	Application.LogMessage("%s | 'TCPServer_start_Init' called!" % Constants.name, siConstants.siVerbose)
	return True

def TCPServer_start_Execute():
	Application.LogMessage("%s | 'TCPServer_start_Execute' called!" % Constants.name, siConstants.siVerbose)
	_startServer()
	return True

def TCPServer_stop_Init(context):
	Application.LogMessage("%s | 'TCPServer_stop_Init' called!" % Constants.name, siConstants.siVerbose)
	return True

def TCPServer_stop_Execute():
	Application.LogMessage("%s | 'TCPServer_stop_Execute' called!" % Constants.name, siConstants.siVerbose)
	_stopServer()
	return True

def TCPServer_timerEvent_OnEvent(context):
	# Application.LogMessage("%s | 'TCPServer_timerEvent' called!" % Constants.name, siConstants.siVerbose)
	Runtime.requestsHandler.processData()
	return False

def TCPServer_Init(context):
	menu = context.Source;
	menu.AddCallbackItem("TCPServer Preferences", "TCPServer_Preferences_Clicked")
	return True

def TCPServer_Preferences_Clicked(context):
	Application.SIAddProp("TCPServer_property", "Scene_Root", siConstants.siDefaultPropagation)
	Application.InspectObj("TCPServer_property", "", "TCPServer_property")
	return True

def TCPServer_property_Define(context):
	property = context.Source
	property.AddParameter2("Logo_siString", siConstants.siString)
	property.AddParameter2("Address_siString", siConstants.siString, Runtime.address)
	property.AddParameter2("Port_siInt", siConstants.siInt4, Runtime.port, 0, 65535, 0, 65535)
	property.AddParameter2("RequestsHandlers_siInt",
							siConstants.siInt4,
							_getRequestsHandlers().index(Runtime.requestsHandler))
	return True

def TCPServer_property_DefineLayout(context):
	layout = context.Source
	layout.Clear()

	Logo_siControlBitmap = layout.AddItem("Logo_siString", "", siConstants.siControlBitmap)
	Logo_siControlBitmap.SetAttribute(siConstants.siUIFilePath, os.path.join(__sipath__, Constants.logo))
	Logo_siControlBitmap.SetAttribute(siConstants.siUINoLabel, True)

	layout.AddGroup("Server", True, 0)
	layout.AddItem("Address_siString", "Address")
	layout.AddItem("Port_siInt", "Port")
	requestsHandlers = [requestsHandler.__name__ for requestsHandler in _getRequestsHandlers()]
	layout.AddEnumControl("RequestsHandlers_siInt",
						sum(map(list, zip(requestsHandlers,requestsHandlers)), []),
						"Requests Handlers", siConstants.siControlCombo)
	layout.EndGroup()

	layout.AddGroup()
	layout.AddRow()
	layout.AddButton("Start_Server_button", "Start TCPServer")
	layout.AddGroup()
	layout.EndGroup()
	layout.AddButton("Stop_Server_button", "Stop TCPServer")
	layout.EndRow()
	layout.EndGroup()
	return True

def TCPServer_property_Address_siString_OnChanged():
	module = _getModule()
	if not module:
		return

	module.Runtime.address = PPG.Address_siString.Value
	module._storeSettings()
	module._restartServer()
	return True

def TCPServer_property_Port_siInt_OnChanged():
	module = _getModule()
	if not module:
		return

	module.Runtime.port = PPG.Port_siInt.Value
	module._storeSettings()
	module._restartServer()
	return True

def TCPServer_property_RequestsHandlers_siInt_OnChanged():
	module = _getModule()
	if not module:
		return

	module.Runtime.requestsHandler = getattr(_getModule(),
	_getRequestsHandlers()[PPG.RequestsHandlers_siInt.Value].__name__)
	module._storeSettings()
	module._restartServer()
	return True

def TCPServer_property_Start_Server_button_OnClicked():
	module = _getModule()
	if not module:
		return

	module._startServer()
	return True

def TCPServer_property_Stop_Server_button_OnClicked():
	module = _getModule()
	if not module:
		return

	module._stopServer()
	return True

def _registerSettingsProperty():
	if not Application.Preferences.Categories(Constants.settings):
		property = Application.ActiveSceneRoot.AddCustomProperty(Constants.settings);
		property.AddParameter2("Address_siString", siConstants.siString, Constants.defaultAddress)
		property.AddParameter2("Port_siInt", siConstants.siInt4, Constants.defaultPort, 0, 65535, 0, 65535)
		property.AddParameter2("RequestsHandler_siInt",
								siConstants.siInt4,
								_getRequestsHandlers().index(Constants.defaultRequestsHandler))
		Application.InstallCustomPreferences("TCPServer_settings_property", "TCPServer_settings_property")
	return True

def _storeSettings():
	if Application.Preferences.Categories(Constants.settings):
		Application.preferences.SetPreferenceValue("%s.Address_siString" % Constants.settings, Runtime.address)
		Application.preferences.SetPreferenceValue("%s.Port_siInt" % Constants.settings, Runtime.port)
		Application.preferences.SetPreferenceValue("%s.RequestsHandler_siInt" % Constants.settings, _getRequestsHandlers().index(Runtime.requestsHandler))
	return True

def _restoreSettings():
	if Application.Preferences.Categories(Constants.settings):
		Runtime.address = str(Application.preferences.GetPreferenceValue("%s.Address_siString" % Constants.settings))
		Runtime.port = int(Application.preferences.GetPreferenceValue("%s.Port_siInt" % Constants.settings))
		Runtime.requestsHandler = _getRequestsHandlers()[int(Application.preferences.GetPreferenceValue("%s.RequestsHandler_siInt" % Constants.settings))]
	return True

def _getServer(address, port, requestsHandler):
	return TCPServer(address, port, requestsHandler)

def _startServer():
	if Runtime.server:
		if Runtime.server.online:
			Application.LogMessage("%s | The server is already online!" % Constants.name, siConstants.siWarning)
			return

	Runtime.server = _getServer(Runtime.address, Runtime.port, Runtime.requestsHandler)
	Runtime.server.start()
	_setServerStatusFile(active=1, address=Runtime.address, port=Runtime.port, handler=Runtime.requestsHandler)
	return True

def _stopServer():
	if Runtime.server:
		if not Runtime.server.online:
			Application.LogMessage("%s | The server is not online!" % Constants.name, siConstants.siWarning)
			return

	Runtime.server and Runtime.server.stop()
	_setServerStatusFile(active=0)
	return True

def _restartServer():
	if Runtime.server:
		Runtime.server.online and _stopServer()

	_startServer()
	return True

def _getModule():
	# Garbage Collector wizardry to retrieve the actual module object.
	import gc
	for object in gc.get_objects():
		if not hasattr(object,"__uid__"):
			continue

		if getattr(object, "__uid__") == __uid__:
			return object

def _getRequestsHandlers():
	# Module introspection to retrieve the requests handlers classes.
	module = _getModule()
	requestsHandlers = []
	for attribute in dir(module):
		object = getattr(module, attribute)
		if not inspect.isclass(object):
			continue

		if issubclass(object, SocketServer.BaseRequestHandler):
			requestsHandlers.append(object)
	return sorted(requestsHandlers, key=lambda x:x.__name__)
