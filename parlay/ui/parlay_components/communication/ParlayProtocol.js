function ParlayProtocolFactory(ParlaySocket, ParlayEndpoint, $q) {
    
    function ParlayProtocol(configuration) {
        "use strict";
        this.id = "UI";
        this.protocol_name = configuration.name;
        this.type = configuration.protocol_type;
        this.available_endpoints = [];
        this.active_endpoints = [];
        this.log = [];
        this.fields = {};
        
        this.listeners = {};
        
        // Objects that inherit from this ParlayProtocol's prototype can set their own endpoint_factory.
        this.endpoint_factory = ParlayEndpoint;
    }
    
    /**
	 * Returns name of protocol.
	 * @returns {String} protocol name
	 */
    ParlayProtocol.prototype.getName = function () {
        return this.protocol_name;
    };
    
    /**
	 * Returns type of protocol.
	 * @returns {String} protocol type
	 */
    ParlayProtocol.prototype.getType = function () {
        return this.type;
    };
    
    /**
	 * Returns available endpoints in protocol.
	 * @returns {Array} available endpoints
	 */
    ParlayProtocol.prototype.getAvailableEndpoints = function () {
        return this.available_endpoints;
    };
    
    /**
	 * Returns all messages that have been collected by the protocol.
	 * @returns {Array} - messages collected by protocol.
	 */
    ParlayProtocol.prototype.getLog = function () {
        return this.log;
    };
    
    /**
	 * Records a listener's deregistration function with the protocol. 
	 * We want to record this function so that when the protocol is closed can clear all onMessage listeners that are relevant to this protocol.
	 * @param {Object} topics - Map of key/value pairs.
	 * @param {Function} deregistrationFn - Function returned from ParlaySocket that will cancel the onMessage callback.
	 */
    ParlayProtocol.prototype.registerListener = function (topics, deregistrationFn) {
	    var topics_string = JSON.stringify(topics);
	    
	    this.listeners[topics_string] = deregistrationFn;
	    
	    return function() {
		  	this.listeners[topics_string]();
		  	delete this.listeners[topics_string];  
	    }.bind(this);
    };
    
    /**
	 * @param {Object} response_topics - Map of key/value pairs.
	 * @param {Function} callback - Callback to invoke upon receipt of response.
	 * @param {Boolean} verbose - If true full response is given to callback, otherwise a reduced Object is returned.
	 * @returns {Function} - Listener deregistration.
	 */
    ParlayProtocol.prototype.onMessage = function(response_topics, callback, verbose) {
	    var topics = Object.keys(response_topics).reduce(function (accumulator, key) {
		   	accumulator[key] = response_topics[key];
		   	return accumulator;
	    }, {
	        TO: this.id
        });
        
        return this.registerListener(topics, ParlaySocket.onMessage(topics, callback, verbose));
    };
    
    /**
	 * Sends message through ParlaySocket.
	 * @param {Object} topics - Map of key/value pairs.
	 * @param {Object} contents - Map of key/value pairs.
	 * @param {Object} response_contents - Map of key/value pairs.
	 * @returns {$q.defer.Promise} - Resolved if ParlaySocket receives a response, rejected if an error occurs during send.
	 */
    ParlayProtocol.prototype.sendMessage = function (topics, contents, response_topics) {
        return $q(function(resolve, reject) {
	        try {
		    	ParlaySocket.sendMessage(topics, contents, response_topics, resolve);    
	        }            
            catch (error) {
	            reject(error);
            }
        }.bind(this));
    };
    
    /**
	 * Will be called on protocol open.
	 */
    ParlayProtocol.prototype.onOpen = function () {
        // Ensure that we record all messages address to the UI.
        this.onMessage({}, function (response) {
	        this.log.push(response);
        }.bind(this), true);
    };
    
    /**
	 * Will be called on protocol close.
	 */
    ParlayProtocol.prototype.onClose = function () {
	    for (var listener in this.listeners) {
		    this.listeners[listener]();
	    }
	    
	    this.listeners = {};
	    
        this.available_endpoints = [];
        this.active_endpoints = [];
    };
    
    /**
	 * Add getter for each key that doesn't currently define one on this protocol instance.
	 * @param {Object} keys - field keys from discovery.
	 */
    ParlayProtocol.prototype.buildFieldMethods = function (keys) {
	    keys.filter(function (key) {
		    return !this[key];
	    }, this).forEach(function (key) {
		    Object.defineProperty(Object.getPrototypeOf(this), key, {
                get: function() { return this.fields[key]; }
            });
	    }, this);
    };
    
    /**
	 * Sets protocol instance fields.
	 * @param {Object} info - field keys from discovery.
	 */
    ParlayProtocol.prototype.buildFields = function (info) {
        this.fields = Object.keys(info).reduce(function (accumulator, key) {
            accumulator[key] = info[key];
            return accumulator;
        }, {});
    };
    
    /**
	 * Returns protocol instance's fields.
	 * @returns {Object} - protocol instance's fields.
	 */
    ParlayProtocol.prototype.getDynamicFieldKeys = function () {
        return Object.keys(this.fields);
    };
    
    /**
	 * Adds endpoints to the protocol instance's available endpoints.
	 * @param {Array} endpoints - Array of the protocol's endpoints.
	 */
    ParlayProtocol.prototype.addEndpoints = function (endpoints) {
        this.available_endpoints = endpoints.map(function (endpoint) {
            return new this.endpoint_factory(endpoint, this);
        }, this);
    };
    
    /**
	 * Distributes discovery message to all relevant methods.
	 * @param {Object} info - Discovery message
	 */
    ParlayProtocol.prototype.addDiscoveryInfo = function (info) {
        this.buildFields(info);
        this.buildFieldMethods(Object.keys(info));
        this.addEndpoints(info.CHILDREN);
    };
    
    return ParlayProtocol;
}

angular.module("parlay.protocols.protocol", ["parlay.socket", "parlay.endpoints.endpoint", "promenade.protocols.directmessage"])
	.factory("ParlayProtocol", ["ParlaySocket", "ParlayEndpoint", "$q", ParlayProtocolFactory]);