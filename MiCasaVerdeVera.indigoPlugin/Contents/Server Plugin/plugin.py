#! /usr/bin/env python
# -*- coding: utf-8 -*-
####################
# Copyright (c) 2012, HomeAutomationPlugins.com All rights reserved.
# http://www.homeautomationplugins.com

import socket
import threading
import thread
import urllib2
import traceback
import xml.dom.minidom
from xml.dom.minidom import parseString
from datetime import datetime, time

################################################################################
# Globals
################################################################################
thePollingUrl = u"/data_request?id=lu_sdata&output_format=xml"
theActionUrl = u"/data_request?id=lu_action&"
theVariableSetUrl = u"/data_request?id=lu_variableset&"
theVariableGetUrl = u"/data_request?id=lu_variableget&"
theUserDataUrl = u"/data_request?id=user_data2&output_format=xml"
thePort = u"3480"

veraIndigoDeviceTypeMap = {
	0									: u"Plugin",
	1									: u"Controller",
	2									: u"Dimmer",
	3									: u"Relay",
	4									: u"SecuritySensor",
	5									: u"Thermostat",
	7									: u"DoorLock",
	8									: u"WindowCovering",
	16									: u"HumiditySensor",
	17									: u"TemperatureSensor",
	18									: u"LightSensor",
	21									: u"PowerMeter"
}

deviceTypeIdToDeviceTypeNameMap = {
	u"SecuritySensor"					: u"Security Sensor",
	u"DoorLock"							: u"Door Lock",
	u"WindowCovering"					: u"Window Covering",
	u"HumiditySensor"					: u"Humidity Sensor",
	u"TemperatureSensor"				: u"Temperature Sensor",
	u"LightSensor"						: u"Light Sensor",
	u"PowerMeter"						: u"Power Meter"
}

def lookupIndigoDeviceTypeFromVeraDeviceType(veraDeviceType):
	return veraIndigoDeviceTypeMap.get(veraDeviceType, u"unknown")

def lookupDeviceTypeNameFromDeviceTypeId(indigoDeviceTypeId):
	return deviceTypeIdToDeviceTypeNameMap.get(indigoDeviceTypeId, u"unknown")

class VeraAutoDetectedDevice(object):
	def __init__(self, veraId, veraParentId, deviceTypeId, veraName, veraBatteryLevel, veraState, veraComment):
		self.veraId, self.veraParentId , self.deviceTypeId, self.veraName, self.veraBatteryLevel, self.veraState, self.veraComment = veraId, veraParentId, deviceTypeId, veraName, veraBatteryLevel, veraState, veraComment

	def toString(self):
		outputBatteryLevel = self.veraBatteryLevel
		if self.veraBatteryLevel == None:
			outputBatteryLevel = 0
		state = self.veraState
		if self.veraState == None:
			state = ""
		comment = self.veraComment
		if self.veraComment == None:
			comment = ""
		return "Device. VeraId: " + str(self.veraId) + " ParentId: " + str(self.veraParentId) + " Type: " + self.deviceTypeId + " Battery Level: " + str(outputBatteryLevel) + " State: " + state + " Comment: " + comment + " - " + self.veraName
		
class VeraAutoDetectedScene(object):
	def __init__(self, veraId, veraName):
		self.veraId = veraId
		self.veraName = veraName
		
	def toString(self):
		return "Scene.  VeraId: " + str(self.veraId) + " - " + self.veraName

################################################################################
class Plugin(indigo.PluginBase):

	def __init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs): 
		indigo.PluginBase.__init__(self, pluginId, pluginDisplayName, pluginVersion, pluginPrefs)
		self.debug = pluginPrefs.get("verboseDebug", False)
		if pluginPrefs.has_key("useSimpleThreading"):
			self.useSimpleThreading = pluginPrefs.get("useSimpleThreading", False)
		else:
			self.useSimpleThreading = False
		self.veraSceneDict = {}
		self.veraDeviceDict = {}
		self.deviceDict = []
		self.lastLoadTime = "0"
		self.lastDataVersion = "0"
		self.timeout = 20
		self.hasDisplayedStatusError = False
		self.updateStateOnDeviceCreated = True

		# timeout in seconds
		socket.setdefaulttimeout(self.timeout)
		
	def deviceConfigListGenerator(self, filter="", valuesDict=None, typeId="", targetId=0):
		if filter == "PowerMeter":
			return [("%d" % (device.veraId), "%s (#%d)" % (device.veraName, device.veraId))
				for device in sorted(self.veraDeviceDict.values(), key=lambda c: (c.veraName))
				if ((device.deviceTypeId == filter) or (device.deviceTypeId == "Dimmer") or (device.deviceTypeId == "Relay"))]
		else:
			return [("%d" % (device.veraId), "%s (#%d)" % (device.veraName, device.veraId))
				for device in sorted(self.veraDeviceDict.values(), key=lambda c: (c.veraName))
				if (device.deviceTypeId == filter)]
		
	def sceneConfigListGenerator(self, filter="", valuesDict=None, typeId="", targetId=0):
		return [("%d" % (scene.veraId), "%s (#%d)" % (scene.veraName, scene.veraId))
			for scene in sorted(self.veraSceneDict.values(), key=lambda c: (c.veraName))]
	
	def validateDeviceConfigUi(self, valuesDict, typeId, devId):
		if valuesDict["veraId"] == "":
			errorDict = indigo.Dict()
			errorDict["veraId"] = "Please select a Z-Wave device from the list of devices retrieved from Vera"
			return (False, valuesDict, errorDict)
			
		valuesDict["address"] = "#" + valuesDict["veraId"]
		return (True, valuesDict)
		
	def validatePrefsConfigUi(self, valuesDict):
		if (self.pluginPrefs.has_key("host") == False) or (self.pluginPrefs["host"] != valuesDict.get("host", False)):
			indigo.server.log("Vera host name changed so retrieving full status from new Vera at " + valuesDict.get("host", False))
			self.lastLoadTime = "0"
			self.lastDataVersion = "0"
			
			thread.start_new_thread(self.retrieveAndParseStatus, (valuesDict.get("host", False), False, False, None))
		return True
		
	def closedPrefsConfigUi(self, valuesDict, userCancelled):
		if not userCancelled:
			if self.debug != valuesDict.get("verboseDebug", False):
				self.debug = valuesDict.get("verboseDebug", False)
				if self.debug:
					indigo.server.log("Verbose debug logging enabled")
				else:
					indigo.server.log("Verbose debug logging disabled")
					
			if self.useSimpleThreading != valuesDict.get("useSimpleThreading", False):
				self.useSimpleThreading = valuesDict.get("useSimpleThreading", False)
				if self.useSimpleThreading:
					indigo.server.log("Simple threading model enabled (reload required)")
				else:
					indigo.server.log("Simple threading model disabled (reload required)")
						
	def deviceCreated(self, dev):
		if self.debug:
			indigo.server.log(dev.name + " created")
		
		deviceWasAutoCreated = False
		
		localPropsCopy = dev.pluginProps
		if dev.pluginProps.has_key("autoCreatedDevice"):
			if localPropsCopy["autoCreatedDevice"] == True:
				deviceWasAutoCreated = True

		self.updateDeviceState(dev, "batteryLevel", 0)
		self.updateDeviceState(dev, "wakeupStatus", "unknown")
		self.updateDeviceState(dev, "trippedState", "inactive")
		self.updateDeviceState(dev, "humidityLevel", 0)
		self.updateDeviceState(dev, "lightLevel", 0)
		self.updateDeviceState(dev, "watts", 0.0)
		self.updateDeviceState(dev, "temperature", 0)
		self.updateDeviceState(dev, "humidityLevel", 0)
		
		if dev.enabled:
			self.deviceStartComm(dev)
		
			if indigo.devices[dev.id].enabled:
			
				if deviceWasAutoCreated == False:
					self.retrieveAndParseStatus(self.pluginPrefs["host"], True, False, None)

	def deviceUpdated(self, origDev, newDev):
		if newDev.pluginProps.has_key("veraId"):
			fullStateRefresh = False
			
			origVeraId = ""
			if origDev.pluginProps.has_key("veraId") == True:
				origVeraId = origDev.pluginProps["veraId"]

			if origVeraId != newDev.pluginProps["veraId"]:
				indigo.server.log("Vera device changed for " + newDev.name + " updating state from Vera...")
				
				#the VeraId has changed for this device, so we need to update the device with the parentId of the new device
				localPropsCopy = newDev.pluginProps
				localPropsCopy.update({"parentId":str(self.veraDeviceDict[int(newDev.pluginProps["veraId"])].veraParentId)})
				newDev.replacePluginPropsOnServer(localPropsCopy)

				#update the model field with the Z-wave manufacturer/model info (if we have it)
				#self.updateDeviceManufacturerModel(newDev)
				
				fullStateRefresh = True
				
			if origDev.enabled != newDev.enabled:
				if newDev.enabled:
					self.deviceStartComm(newDev)
					fullStateRefresh = True
				else:
					self.deviceStopComm(newDev)
			if fullStateRefresh:
				self.retrieveAndParseStatus(self.pluginPrefs["host"], True, False, None)
		else:
			#if we are trying to enable a device, ensure we have a device type, and a veraId
		 	if newDev.enabled:
		 		if newDev.deviceTypeId == "":
		 			indigo.device.enable(newDev, value=False) 
					self.errorLog(newDev.name + " automatically disabled as no device type is set (see device configuration)")
		 		elif newDev.pluginProps.has_key("veraId") == False:
					indigo.device.enable(newDev, value=False) 
					self.errorLog(newDev.name + " automatically disabled as no Z-Wave Vera device is set (see device configuration)")
			elif origDev.enabled == newDev.enabled:
				if newDev.deviceTypeId == "":
					self.errorLog(newDev.name + " has no device type set (see device configuration)")
		 		elif newDev.pluginProps.has_key("veraId") == False:
					self.errorLog(newDev.name + " has no Z-Wave Vera device set (see device configuration)")	
			
	def deviceStartComm(self, dev):
		if dev.id not in self.deviceDict:
			if dev.pluginProps.has_key("veraId"):
				if dev.pluginProps["veraId"] == "":
					indigo.device.enable(dev, value=False) 
					self.errorLog(dev.name + " automatically disabled as no Z-Wave Vera device is set (see device configuration)")
				else:
					self.deviceDict.append(dev.id)
					if self.debug:
						indigo.server.log(dev.name + " communication enabled")
			else:
				indigo.device.enable(dev, value=False) 
				self.errorLog(dev.name + " automatically disabled as no device type is set (see device configuration)")

	def deviceStopComm(self, dev):
		if dev.id in self.deviceDict:
			self.deviceDict.remove(dev.id)
			if self.debug:
				indigo.server.log(dev.name + " communication disabled")
					
	def formatDateTime(self, dateTimeToFormat):
		day = dateTimeToFormat.day
		if 4 <= day <= 20 or 24 <= day <= 30:
			suffix = "th"
		else:
			suffix = ["st", "nd", "rd"][day % 10 - 1]
		return dateTimeToFormat.strftime("%a %d" + suffix + " %b %Y at %H:%M:%S")
							
	def getIndigoDeviceForVeraId(self, veraId):
		for dev in indigo.devices.iter("self"):
			if (dev.deviceTypeId != "Scene"):
				if dev.pluginProps.has_key("veraId") and (dev.pluginProps["veraId"] == str(veraId)):
					return dev
		return None
		
	def getIndigoSceneForVeraId(self, veraId):
		for dev in indigo.devices.iter("com.homeautomationplugins.vera.Scene"):
			if dev.pluginProps.has_key("veraId") and (dev.pluginProps["veraId"] == str(veraId)):
				return dev
		return None
		
	def getUniqueDeviceName(self, seedName):
		seedName = seedName.strip()
		if (seedName not in indigo.devices):
			return seedName
		else:
			counter = 1
			candidate = seedName + " " + str(counter)
			while candidate in indigo.devices:
				counter = counter + 1
				candidate = seedName + " " + str(counter)
			return candidate

	def autoCreateVeraDevices(self):
			
		indigo.server.log("Auto creation started")
			
		if self.pluginPrefs.has_key("host"):
			folderName = "Vera Auto Created Devices"
			folder = None
	
			if (folderName in indigo.devices.folders):
				if self.debug:
					indigo.server.log("Folder named '" + folderName + "' exists, will add auto-created devices here")
				folder = indigo.devices.folders[folderName]
			else:
				if self.debug:
					indigo.server.log("Folder named '" + folderName + "' does not exist, creating one and will add auto-created devices to it")
				folder = indigo.devices.folder.create(folderName)
				
			self.retrieveAndParseStatus(self.pluginPrefs["host"], True, True, folder)
			
			indigo.server.log("Auto creation devices created, refreshing variable values...")
			
			#self.retrieveAndParseVariables()
			
			indigo.server.log("Auto creation complete")
			
		else:
			self.errorLog("You must specify the address of the Vera in the plugin config before Auto Creating Vera Devices")

	def downloadUrl(self, url):
		try:
			if self.debug:
				indigo.server.log("  (" + url + ")")
			f = urllib2.urlopen(url)
			return f.read()
		except urllib2.HTTPError, e:
			if self.debug:
				self.errorLog(u"Error getting data: " + str(e))
			return None
		except urllib2.URLError, e:
			if self.debug:
				self.errorLog(u"Error getting data (unable to open url): " + str(e))
			return None
		except Exception, e:
			if self.debug:
				self.errorLog(u"Unknown error: " + traceback.format_exc())
			return None
			
	def openUrlOnVera(self, url, description):
		result = self.downloadUrl(url)
		if result == None:
			if self.debug:
				self.errorLog(description + " failed")
			else:
				self.errorLog(description + " failed, enable 'full debug output' in Plugin config to diagnose")
		elif result.strip().lower().startswith("error:"):
			self.errorLog(description + " failed '" + result.strip() + "'")
		else:
			dom = parseString(result)
			if dom == None:
				if self.debug:
					self.errorLog(description + " failed parsing XML response")
				else:
					self.errorLog(description + " failed parsing XML response, enable 'full debug output' in Plugin config to diagnose")
			else:
				#Get the JobID from the response
				jobIdNode = dom.getElementsByTagName("JobID")[0]
				if jobIdNode == None:
					if self.debug:
						self.errorLog(description + " failed parsing JobID from XML")
					else:
						self.errorLog(description + " failed parsing JobID from XML, enable 'full debug output' in Plugin config to diagnose")
				else:
					jobId = str(jobIdNode.childNodes[0].nodeValue)
					indigo.server.log(description + " (job ID " + jobId + ")")
		
	def sendActionToVera(self, dev, queryString, description):
		url = "http://" + self.pluginPrefs["host"] + ":" + thePort + theActionUrl + "DeviceNum=" + dev.pluginProps["veraId"] + "&" + queryString
		description = dev.name + " " + description
		thread.start_new_thread(self.openUrlOnVera, (url, description))
		
	def getVariableValueFromVera(self, dev, queryString):
		theUrl = "http://" + self.pluginPrefs["host"] + ":" + thePort + theVariableGetUrl + "DeviceNum=" + dev.pluginProps["veraId"] + "&" + queryString
		#if self.debug:
		#	indigo.server.log("getVariableValueFromVera: " + dev.name)
		return self.downloadUrl(theUrl)

	def retrieveAndParseStatus(self, ipOfVera, forceFull, autoCreateDevices, autoCreateDevicesFolder):
		createDevices = True
		
		#if we are creating devices then it implies we should be requesting a full set of data from Vers
		if autoCreateDevices == None:
			createDevices = False
		elif autoCreateDevices == False:
			createDevices = False
		elif autoCreateDevices == True:
			createDevices = True
			forceFull = True
		
		theUrl = "http://" + ipOfVera + ":" + thePort + thePollingUrl 
		
		if forceFull:
			theUrl = theUrl + "&loadtime=0&dataversion=0"
		else:
			theUrl = theUrl + "&loadtime=" + self.lastLoadTime + "&dataversion=" + self.lastDataVersion
			
		theUrl = theUrl + "&timeout=" + str(int(self.timeout / 2)) + "&minimumdelay=50"
		
		theXml = self.downloadUrl(theUrl)

		dom = None
		
		if theXml is None:
			if self.hasDisplayedStatusError == False:
				self.errorLog(u"Failed to retrieve status from Vera, will continue to retry...")
				self.hasDisplayedStatusError = True
			return None
		else:
			if self.debug:
				indigo.server.log(u"Retrieved status from " + theUrl)
				indigo.server.log(u"'''" + theXml + "'''")
				
			dom = parseString(theXml)
			
			self.hasDisplayedStatusError = False
			
			#Update local load time and dataversion
			rootNode = dom.getElementsByTagName("root")[0]
			self.lastLoadTime = rootNode.getAttributeNode("loadtime").nodeValue
			self.lastDataVersion = rootNode.getAttributeNode("dataversion").nodeValue

		if dom != None:

			#If Vera is reporting a full status to us we update our local cache of Vera devices and scenes
			sync = int(dom.getElementsByTagName("root")[0].getAttributeNode("full").nodeValue) == 1
		
			if sync:
				self.veraSceneDict.clear()
				self.veraDeviceDict.clear()
			
			#SCENES
			for sceneNode in dom.getElementsByTagName("scene"):
				veraId = int(sceneNode.getAttributeNode("id").nodeValue)
				name = sceneNode.getAttributeNode("name")
				if name != None:
					name = name.nodeValue
				
				scene = self.getIndigoSceneForVeraId(veraId)
				
				if sync:
					if not self.veraSceneDict.has_key(veraId):
						self.veraSceneDict[veraId] = VeraAutoDetectedScene(veraId, name)
						
				#auto-creating scenes
				if createDevices:
					if scene == None:
						scene = indigo.device.create(protocol=indigo.kProtocol.Plugin,
							address="#" + str(veraId),
							name=self.getUniqueDeviceName(name), 
							description="", 
							pluginId="com.homeautomationplugins.vera",
							deviceTypeId="Scene",
							folder=autoCreateDevicesFolder,
							props={"autoCreatedDevice":True,"veraId":str(veraId),"address":"#" + str(veraId)})
						indigo.server.log("Vera scene '" + name + "' (#" + str(veraId) + ") created as Indigo device '" + scene.name + "'")
					else:
						indigo.server.log("Vera scene '" + name + "' (#" + str(veraId) + ") skipped as it's already mapped to Indigo device '" + scene.name + "'")
						
				if (scene != None) and (scene.id in self.deviceDict or createDevices):	
					sceneActive = int(sceneNode.getAttributeNode("active").nodeValue)
					if sceneActive == 0:
						self.updateDeviceState(scene,"activeState", "inactive")
					else:
						self.updateDeviceState(scene,"activeState", "active")

			#DEVICES
			for deviceNode in dom.getElementsByTagName("device"):
				indigoDeviceType = "unknown"			
				veraId = int(deviceNode.getAttributeNode("id").nodeValue)
				veraParentId = int(deviceNode.getAttributeNode("parent").nodeValue)
				name = deviceNode.getAttributeNode("name")
				if name != None:
					name = name.nodeValue
				category = deviceNode.getAttributeNode("category")
				if category != None:
					category = int(category.nodeValue)
					indigoDeviceType = lookupIndigoDeviceTypeFromVeraDeviceType(category)
				room = deviceNode.getAttributeNode("room")
				if room != None:
					room = int(room.nodeValue)
					
				#BATTERY LEVEL
				batterylevel = deviceNode.getAttributeNode("batterylevel")
				if batterylevel != None:
					batterylevel = float(batterylevel.nodeValue)
					
				#STATE
				state = deviceNode.getAttributeNode("state")
				if state != None:
					state = int(state.nodeValue)
					if state == -1:
						state = "ok"
					elif (state == 0) or (state == 1) or (state == 5) or (state == 6):
						state = "pending"
					elif (state == 2) or (state == 3):
						state = "error"
					elif state == 4:
						state = "success"
						
				#COMMENT
				comment = deviceNode.getAttributeNode("comment")
				if comment != None:
					comment = comment.nodeValue.strip()
				
				device = self.getIndigoDeviceForVeraId(veraId)
				
				indigoDeviceTypeMatchesVeraDeviceType = True

				if sync:
					if not self.veraDeviceDict.has_key(veraId):
						if indigoDeviceType != "unknown":
							self.veraDeviceDict[veraId] = VeraAutoDetectedDevice(veraId, veraParentId, indigoDeviceType, name, batterylevel, state, comment)

				#auto-creating devices
				if createDevices and (indigoDeviceType != "unknown"):
					if device == None:
						#there are some specific extra properties for Dimmers, so they are a special case
						devProps = None
						if indigoDeviceType == "Dimmer":
							devProps = {"autoCreatedDevice":True,"veraId":str(veraId),"address":"#" + str(veraId),"parentId":str(veraParentId),"persistLastBrightness":True,"lastBrightness":"0"}
						else:
							devProps = {"autoCreatedDevice":True,"veraId":str(veraId),"address":"#" + str(veraId),"parentId":str(veraParentId)}
							
						device = indigo.device.create(protocol=indigo.kProtocol.Plugin,
							address="#" + str(veraId),
							name=self.getUniqueDeviceName(name), 
							description="", 
							pluginId="com.homeautomationplugins.vera",
							deviceTypeId=indigoDeviceType,
							folder=autoCreateDevicesFolder,
							props=devProps)
						indigo.server.log("Vera device '" + name + "' (#" + str(veraId) + ") created as Indigo device '" + device.name + "'")
					else:
						indigo.server.log("Vera device '" + name + "' (#" + str(veraId) + ") skipped as it's already mapped to Indigo device '" + device.name + "'")
						

				#when we change the Vera IP address Vera ID #11 may change from one type of device to another
				#in the case where Indigo and Vera do not match we disable the device and write out an error
				if (device != None) and (indigoDeviceType != "unknown") and (category != None):
					if device.deviceTypeId != indigoDeviceType:
						indigoDeviceTypeMatchesVeraDeviceType = False
						self.errorLog(device.name + " device type (" + device.deviceTypeId + ") does not match that reported from Vera (" + indigoDeviceType + ") for Vera ID #" + str(veraId))
				if indigoDeviceType == "unknown" and category != None:
					indigoDeviceTypeMatchesVeraDeviceType = False
					
				if (device != None) and (((device.id in self.deviceDict) and (indigoDeviceTypeMatchesVeraDeviceType == True)) or createDevices):
					
					parentVeraAutoDetectedDevice = None
					if (device.pluginProps.has_key("parentId")) and (device.pluginProps["parentId"] != "") and (device.pluginProps["parentId"] != None) and (device.pluginProps["parentId"] != "1") and (device.pluginProps["parentId"] != device.pluginProps["veraId"]):
						parentId = int(device.pluginProps["parentId"])
						if self.veraDeviceDict.has_key(parentId):
							parentVeraAutoDetectedDevice = self.veraDeviceDict[parentId]
					
					#if parentVeraAutoDetectedDevice != None:
					#	indigo.server.log("* " + device.name + " has parent " + parentVeraAutoDetectedDevice.veraName)
					#else:
					#	indigo.server.log("* " + device.name + " has no parent")

					#BATTERY LEVEL
					if parentVeraAutoDetectedDevice != None:
						#get battery level from parent
						parentBatteryLevel = parentVeraAutoDetectedDevice.veraBatteryLevel
						if parentBatteryLevel != None:
							self.updateDeviceState(device, "batteryLevel", parentBatteryLevel)
					else:
						#take battery level from this device
						if batterylevel != None:
							self.updateDeviceState(device, "batteryLevel", batterylevel)
						
					#STATE
					if parentVeraAutoDetectedDevice != None:
						#get state from parent
						parentState = parentVeraAutoDetectedDevice.veraState
						if parentState != None:
							self.updateDeviceState(device, "state", parentState)
					else:
						#take state from this device
						if state != None:
							self.updateDeviceState(device, "state", state)
							
					#COMMENT
					if parentVeraAutoDetectedDevice != None:
						#get comment from parent
						parentComment = parentVeraAutoDetectedDevice.veraComment
						if parentComment != None:
							self.updateDeviceState(device, "comment", parentComment)
					else:
						#take comment from this device
						if comment != None:
							self.updateDeviceState(device, "comment", comment)
					
						
						
							
					if device.deviceTypeId == "Plugin":
						watts = deviceNode.getAttributeNode("watts")
						if watts != None:
							self.updateDeviceState(device,"watts", float(watts.nodeValue))
							
					elif device.deviceTypeId == "Dimmer":
						status = deviceNode.getAttributeNode("status")
						if status != None:
							if int(status.nodeValue) == 0:
								self.updateDeviceState(device,"onOffState", False)
								self.updateDeviceState(device,"brightnessLevel", 0)
							else:
								self.updateDeviceState(device,"onOffState", True)
								level = deviceNode.getAttributeNode("level")
								if level != None:
									self.updateDeviceState(device,"brightnessLevel", int(level.nodeValue))
						watts = deviceNode.getAttributeNode("watts")
						if watts != None:
							self.updateDeviceState(device,"watts", float(watts.nodeValue))
					
					elif device.deviceTypeId == "Relay":
						status = deviceNode.getAttributeNode("status")
						if status != None:
							if int(status.nodeValue) == 0:
								self.updateDeviceState(device,"onOffState", False)
							else:
								self.updateDeviceState(device,"onOffState", True)
						watts = deviceNode.getAttributeNode("watts")
						if watts != None:
							self.updateDeviceState(device,"watts", float(watts.nodeValue))
							
					elif device.deviceTypeId == "DoorLock":		
						status = deviceNode.getAttributeNode("locked")
						if status != None:
							if int(status.nodeValue) == 0:
								self.updateDeviceState(device,"onOffState", False)
							else:
								self.updateDeviceState(device,"onOffState", True)
						else:
							locked = deviceNode.getAttributeNode("locked")
							if locked != None:
								if int(locked.nodeValue) == 0:
									self.updateDeviceState(device,"onOffState", False)
								else:
									self.updateDeviceState(device,"onOffState", True)
				
					elif device.deviceTypeId == "WindowCovering":
						level = deviceNode.getAttributeNode("level")
						if level != None:
							self.updateDeviceState(device,"brightnessLevel", int(level.nodeValue))
						
						status = deviceNode.getAttributeNode("status")
						if status != None:
							if int(status.nodeValue) == 0:
								self.updateDeviceState(device,"onOffState", False)
							else:
								self.updateDeviceState(device,"onOffState", True)
								
					elif device.deviceTypeId == "SecuritySensor":		
						tripped = deviceNode.getAttributeNode("tripped")
						if tripped != None:
							if int(tripped.nodeValue) == 1:
								self.updateDeviceState(device,"trippedState", "active")
							else:
								self.updateDeviceState(device,"trippedState", "inactive")

					elif device.deviceTypeId == "Thermostat":		
						temp = deviceNode.getAttributeNode("temperature")
						if temp != None:
							temp = int(temp.nodeValue)
						self.updateDeviceState(device,"temperature", temp)
						
						fanMode = deviceNode.getAttributeNode("fan")
						if fanMode != None:
							fanMode = int(fanMode.nodeValue)
							if fanMode == 1:
								self.updateDeviceState(device, "fanMode", "always on")
							else:
								self.updateDeviceState(device, "fanMode", "auto")

						mode = deviceNode.getAttributeNode("mode")
						if mode != None:
							mode = mode.nodeValue
							if mode == "Off":
								self.updateDeviceState(device, "hvacOperationMode", 3)
							elif mode == "HeatOn":
								self.updateDeviceState(device, "hvacOperationMode", 1)
							elif mode == "CoolOn":
								self.updateDeviceState(device, "hvacOperationMode", 0)
							elif mode == "AutoChangeOver":
								self.updateDeviceState(device, "hvacOperationMode", 6)
		
						heatSp = deviceNode.getAttributeNode("heatsp")
						if heatSp != None:
							heatSp = float(heatSp.nodeValue)
							self.updateDeviceState(device, "setpointHeat", heatSp)
							
						coolSp = deviceNode.getAttributeNode("coolsp")
						if coolSp != None:
							coolSp = float(coolSp.nodeValue)
							self.updateDeviceState(device, "setpointCool", coolSp)
								
					elif device.deviceTypeId == "HumiditySensor":		
						humidity = deviceNode.getAttributeNode("humidity")
						if humidity != None:
							self.updateDeviceState(device,"humidityLevel", float(humidity.nodeValue))
							
					elif device.deviceTypeId == "TemperatureSensor":		
						temp = deviceNode.getAttributeNode("temperature")
						if temp != None:
							self.updateDeviceState(device,"temperature", float(temp.nodeValue))
							
					elif device.deviceTypeId == "LightSensor":
						light = deviceNode.getAttributeNode("light")
						if light != None:
							self.updateDeviceState(device,"lightLevel", float(light.nodeValue))
							
					elif device.deviceTypeId == "PowerMeter":
						watts = deviceNode.getAttributeNode("watts")
						if watts != None:
							self.updateDeviceState(device,"watts", float(watts.nodeValue))
							
				
	
			if sync and self.debug:
				indigo.server.log(" ")
				indigo.server.log("Scenes")
				for veraId in self.veraSceneDict.keys():
					cachedScene = self.veraSceneDict[veraId]
					indigo.server.log(" " + self.veraSceneDict[veraId].toString())
				indigo.server.log(" ")
				indigo.server.log("Devices")
				for veraId in self.veraDeviceDict.keys():
					indigo.server.log(" " + self.veraDeviceDict[veraId].toString())
				indigo.server.log(" ")
							
			return True
			
	def runConcurrentThread(self):
		if self.debug:
			indigo.server.log("Starting concurrent tread")
		try:
			def statusLoop():
				while True:
					if self.pluginPrefs.has_key("host"):
						try:
							if self.retrieveAndParseStatus(self.pluginPrefs["host"], False, False, None) == None:
								self.sleep(5)
						except Exception, e:
							self.errorLog("statusLoop Error: " + traceback.format_exc())
							sel.sleep(5)

			if self.useSimpleThreading == False:
				thread.start_new_thread(statusLoop, ())
			
			while True:
				if self.useSimpleThreading == True:
					if self.retrieveAndParseStatus(self.pluginPrefs["host"], False, False, None) == None:
						self.sleep(5)
				else:
					self.sleep(5)
				
		except self.StopThread:
			pass

	def updateDeviceState(self,device,state,newValue):
		try:
			if device.states.has_key(state):
				if (newValue != device.states[state]):
					device.updateStateOnServer(key=state, value=newValue)
		except Exception, e:
			self.errorLog(u"Error setting state " + state + " on device " + device.name + ". " + traceback.format_exc())

	def actionControlDimmerRelay(self, action, dev):
		if action.deviceAction == indigo.kDeviceAction.TurnOff:
			self.turnOff(dev)
	
		elif action.deviceAction == indigo.kDeviceAction.TurnOn:
			self.turnOn(dev)

		elif action.deviceAction == indigo.kDeviceAction.Toggle:
			if dev.onState:
				self.turnOff(dev)
			else:
				self.turnOn(dev)

		elif action.deviceAction == indigo.kDeviceAction.SetBrightness:
			self.setBrightness(dev, action.actionValue)

		elif action.deviceAction == indigo.kDeviceAction.BrightenBy:
			newBrightness = dev.brightness + action.actionValue
			if newBrightness == 0:
				newBrightness = action.actionValue
			if newBrightness > 100:
				newBrightness = 100
			self.setBrightness(dev, newBrightness)

		elif action.deviceAction == indigo.kDeviceAction.DimBy:
			newBrightness = dev.brightness - action.actionValue
			if newBrightness < 0:
				newBrightness = 0
			self.setBrightness(dev, newBrightness)
			
	def turnOff(self, dev):
		if dev.deviceTypeId == "DoorLock":
			self.sendActionToVera(dev, "serviceId=urn:micasaverde-com:serviceId:DoorLock1&action=SetTarget&newTargetValue=0", "unlock")
		else:
			if dev.deviceTypeId == "Dimmer":
				if dev.pluginProps["persistLastBrightness"] == True:
					localPropsCopy = dev.pluginProps
					if self.debug:
						indigo.server.log(dev.name + " saving last brightness value of " + str(dev.states["brightnessLevel"]) + "%")
					localPropsCopy.update({"lastBrightness":str(dev.states["brightnessLevel"])})
					dev.replacePluginPropsOnServer(localPropsCopy)
			self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:SwitchPower1&action=SetTarget&newTargetValue=0", "turn off")
			
	def turnOn(self, dev):
		if dev.deviceTypeId == "DoorLock":
			self.sendActionToVera(dev, "serviceId=urn:micasaverde-com:serviceId:DoorLock1&action=SetTarget&newTargetValue=1", "lock")
		else:
			if dev.deviceTypeId == "Dimmer":
				if (dev.pluginProps["persistLastBrightness"] == True) and (int(dev.pluginProps["lastBrightness"]) > 0):
					if self.debug:
						indigo.server.log(dev.name + " restoring last brightness value of " + dev.pluginProps["lastBrightness"])
					self.setBrightness(dev, int(dev.pluginProps["lastBrightness"]))
				else:
					self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:SwitchPower1&action=SetTarget&newTargetValue=1", "turn on")	
			else:
				self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:SwitchPower1&action=SetTarget&newTargetValue=1", "turn on")	
				
	def setBrightness(self, dev, newBrightness):
		description = ""
		if dev.deviceTypeId == "Dimmer":
			description = "set brightness to " + str(newBrightness) + "%"
			if (dev.pluginProps["persistLastBrightness"] == True) and (int(dev.pluginProps["lastBrightness"]) > 0):
				localPropsCopy = dev.pluginProps
				if self.debug:
					indigo.server.log(dev.name + " erasing last brightness value of " + dev.pluginProps["lastBrightness"] + "%")
				localPropsCopy.update({"lastBrightness":0})
				dev.replacePluginPropsOnServer(localPropsCopy)
		elif dev.deviceTypeId == "WindowCovering":
			description = "set window covering to " + str(newBrightness) + "%"
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:Dimming1&action=SetLoadLevelTarget&newLoadlevelTarget=" + str(newBrightness), description)
			
	def actionControlThermostat(self, action, dev):
		###### SET HVAC MODE ######
		if action.thermostatAction == indigo.kThermostatAction.SetHvacMode:
			if action.actionMode == indigo.kHvacMode.Cool or action.actionMode == indigo.kHvacMode.ProgramCool:
				self.setThermostatModeCoolOn(dev)
				
			elif action.actionMode == indigo.kHvacMode.Heat or action.actionMode == indigo.kHvacMode.ProgramHeat:
				self.setThermostatModeHeatOn(dev)
				
			elif action.actionMode == indigo.kHvacMode.HeatCool or action.actionMode == indigo.kHvacMode.ProgramHeatCool:
				self.setThermostatFanModeAuto(dev)
				
			elif action.actionMode == indigo.kHvacMode.Off:
				self.setThermostatModeOff(dev)
				
		###### SET FAN MODE ######
		elif action.thermostatAction == indigo.kThermostatAction.SetFanMode:
			if action.actionMode == indigo.kFanMode.AlwaysOn:
				self.setThermostatFanModeContinuousOn(dev)
				
			elif action.actionMode == indigo.kFanMode.Auto:
				self.setThermostatFanModeAuto(dev)

		###### SET COOL SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.SetCoolSetpoint:
			newSetpoint = action.actionValue
			self.setThermostatCoolSetpoint(dev, newSetpoint)

		###### SET HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.SetHeatSetpoint:
			newSetpoint = action.actionValue
			self.setThermostatHeatSetpoint(dev, newSetpoint)

		###### DECREASE/INCREASE COOL SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.DecreaseCoolSetpoint:
			newSetpoint = dev.coolSetpoint - action.actionValue
			self.setThermostatCoolSetpoint(dev, newSetpoint)

		elif action.thermostatAction == indigo.kThermostatAction.IncreaseCoolSetpoint:
			newSetpoint = dev.coolSetpoint + action.actionValue
			self.setThermostatCoolSetpoint(dev, newSetpoint)

		###### DECREASE/INCREASE HEAT SETPOINT ######
		elif action.thermostatAction == indigo.kThermostatAction.DecreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint - action.actionValue
			self.setThermostatHeatSetpoint(dev, newSetpoint)

		elif action.thermostatAction == indigo.kThermostatAction.IncreaseHeatSetpoint:
			newSetpoint = dev.heatSetpoint + action.actionValue
			self.setThermostatHeatSetpoint(dev, newSetpoint)

	def runScene(self, pluginAction, dev):
		self.sendActionToVera(dev, "serviceId=urn:micasaverde-com:serviceId:HomeAutomationGateway1&action=RunScene&SceneNum=" + dev.pluginProps["veraId"], "run scene")

	def setThermostatHeatSetpoint(self, dev, newValue):
		description = "set heat setpoint to " + str(newValue)
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:TemperatureSetpoint1_Heat&action=SetCurrentSetpoint&NewCurrentSetpoint=" + str(newValue), description)
		
	def setThermostatCoolSetpoint(self, dev, newValue):
		description = "set cool setpoint to " + str(newValue)
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:TemperatureSetpoint1_Cool&action=SetCurrentSetpoint&NewCurrentSetpoint=" + str(newValue), description)

	def setThermostatModeOff(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_UserOperatingMode1&action=SetModeTarget&NewModeTarget=Off", "hvac mode off")								

	def setThermostatModeHeatOn(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_UserOperatingMode1&action=SetModeTarget&NewModeTarget=HeatOn", "hvac mode heat on")

	def setThermostatModeCoolOn(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_UserOperatingMode1&action=SetModeTarget&NewModeTarget=CoolOn", "hvac mode cool on")

	def setThermostatModeAutoChangeOver(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_UserOperatingMode1&action=SetModeTarget&NewModeTarget=AutoChangeOver", "hvac mode auto")
				
	def setThermostatFanModeAuto(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_FanOperatingMode1&action=SetMode&NewMode=Auto", "fan auto")

	def setThermostatFanModeContinuousOn(self, dev):
		self.sendActionToVera(dev, "serviceId=urn:upnp-org:serviceId:HVAC_FanOperatingMode1&action=SetMode&NewMode=ContinuousOn", "fan continuous on")	