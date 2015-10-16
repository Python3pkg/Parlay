 /**
 * If the buffer is valid and available we should force the md-chips controller to push it to the ng-model.
 * @param {NodeList} chipElements - List of HTMLElements mapping to each md-chip.
 */
function pushChipBuffer (chipElements) {
    if (chipElements.length) {
	    var ctrl = angular.element(chipElements[0].querySelector('input')).scope().$mdChipsCtrl;
	    var buffer = ctrl.getChipBuffer();
	    if (buffer !== "") {
			ctrl.appendChip(buffer);
			ctrl.resetChipBuffer();    
	    }			
	}
}

/**
 * Collects and formats the fields available on the given message object.
 * @param {Object} message - message container from the scope.
 * @returns - parsed and formatted StandardEndpoint data.
 */    
function collectMessage (message) {
    return Object.keys(message).reduce(function (accumulator, field) {
        var param_name, field_type;
        
        if (field.indexOf('_') > -1) {
	        var split_field = field.split('_');

            field_type = split_field[split_field.length - 1];

            param_name = split_field.slice(0, split_field.length - 1).join('_');
	    }
	    else {
		    param_name = field;
	    }
	    
	    // If type is Object or Array then turn the JSON string into an actual Object.
	    if (field_type === "ARRAY") accumulator[param_name] = message[field].map(function (chip) { 
		    return !Number.isNaN(chip.value) ? parseInt(chip.value) : chip.value;
		});
	    else if (field_type === "NUMBERS") accumulator[param_name] = message[field].map(parseFloat);
	    else if (angular.isObject(message[field])) accumulator[param_name] = message[field].value;
        else accumulator[param_name] = message[field];
        
        return accumulator;
    }, {});

}

function PromenadeStandardEndpointCardCommandTabController($scope, $timeout, ScriptLogger, ParlayUtility) {
	ParlayBaseTabController.call(this, $scope);
	
	$scope.wrapper = {
		message: {}
	};
	
	this.error = false;
	this.sending = false;
	this.status_message= null;
	
	this.sending_timeout = null;
	
	this.send = function ($event) {
		// Push the buffer into the md-chips ng-model
		if ($event) pushChipBuffer($event.target.querySelectorAll('md-chips'));
	    
	    this.error = false;
	    this.sending = true;
	    
	    try {
	    	var message = collectMessage($scope.wrapper.message);
	    	this.endpoint.sendMessage(message)
		     	.then(function (response) {
			     	
			     	// Use the response to display feedback on the send button.
			        this.status_message = response.STATUS_NAME;
			        
			        // If we still have an outstanding timeout we should cancel it to prevent the send button from flickering.
		            if (sending_timeout !== null) $timeout.cancel(sending_timeout);
		            
		            // Setup a timeout to reset the button to it's default state after a brief period of time.
		        	sending_timeout = $timeout(function () {
			        	sending_timeout = null;
		                this.sending = false;
		                this.status_message = null;
		            }, 500);
		            
		        }.bind(this)).catch(function (response) {
			        this.sending = false;
			        this.error = true;
			        this.status_message = response.STATUS_NAME;
		        }.bind(this));
		    
		    // Put the Python equivalent command in the log.
	        ScriptLogger.logCommand("SendCommand(" + Object.keys(message).map(function (key) {
		        return typeof message[key] === 'number' ? key + '=' + message[key] : key + "='" + message[key] + "'";
	        }).join(',') + ')');
	    }
	    catch (e) {
	     	this.error = true;
	     	this.status_message = e;   
	    }
	};
	
	// Watch for new fields to fill with defaults.
    $scope.$watchCollection("wrapper.message", function () {
	    Object.keys($scope.wrapper.message).filter(function (key) {
		    // Build an Array with fields that have sub fields.
		    return $scope.wrapper.message[key] !== undefined && $scope.wrapper.message[key].hasOwnProperty("sub_fields");
	    }).map(function (key) {
	        return $scope.wrapper.message[key].sub_fields;
	    }).reduce(function (accumulator, current) {
		    // Join all the sub_fields into a larger Array.
		    return accumulator.concat(current);
	    }, []).filter(function (field) {
		    // Check if the sub field already has a message field entry.
	        return field !== undefined && !$scope.wrapper.message.hasOwnProperty(field.msg_key + '_' + field.input);
	    }).forEach(function (field) {
		    // Fill in the default value in the message Object.
	        $scope.wrapper.message[field.msg_key + '_' + field.input] = ['NUMBERS', 'STRINGS', 'ARRAY'].indexOf(field.input) > -1 ? [] : field.default;
	    });
    });
    
    $scope.$on("$destroy", function () {
		$scope.$parent.deactivateDirective("tabs", "promenadeStandardEndpointCardCommands");
	});
	
}

PromenadeStandardEndpointCardCommandTabController.prototype = Object.create(ParlayBaseTabController.prototype);

function PromenadeStandardEndpointCardCommands() {
	return {
        scope: {
            endpoint: "="
        },
        templateUrl: "../vendor_components/promenade/endpoints/directives/promenade-standard-endpoint-card-commands.html",
        controller: "PromenadeStandardEndpointCardCommandTabController",
        controllerAs: "ctrl",
        bindToController: true
    };
}

function PromenadeStandardEndpointCardCommandContainer(RecursionHelper, ParlayPersistence, ParlayUtility) {
	return {
        scope: {
            wrapper: '=',
            fields: '=',
            commandform: '='
        },
        templateUrl: '../vendor_components/promenade/endpoints/directives/promenade-standard-endpoint-card-command-container.html',
        compile: RecursionHelper.compile,
        controller: function ($scope) {

	        var container = ParlayUtility.relevantScope($scope, 'container').container;
			var directive_name = 'parlayEndpointCard.' + container.ref.name.replace(' ', '_') + '_' + container.uid;
		    
		    ParlayPersistence.monitor(directive_name, "wrapper.message", $scope);
	        
	        $scope.prepChip = function ($chip) {
   			    return {value: $chip};
		    };
	        
	        /**
		     * Checks if the given field has sub fields available.
		     * @param {Object} field - the field we are interested in.
		     * @returns {Boolean} - true if the target field has sub fields available, false otherwise.
		     */
	        $scope.hasSubFields = function (field) {
		        var message_field = $scope.wrapper.message[field.msg_key + '_' + field.input];
		        return message_field !== undefined && message_field !== null && message_field.sub_fields !== undefined;
	        };
	        
	        /**
		     * Returns a given field's sub fields.
		     * @param {Object} field - the field we are interested in.
		     * @returns {Object|Array} - the fields sub fields, may be Object or Array.
		     */
	        $scope.getSubFields = function (field) {
		        return $scope.wrapper.message[field.msg_key + '_' + field.input].sub_fields;
	        };
            
        }
    };
}

angular.module('promenade.endpoints.standardendpoint.commands', ['RecursionHelper', 'parlay.store', 'parlay.navigation.bottombar', 'parlay.utility'])
	.controller('PromenadeStandardEndpointCardCommandTabController', ['$scope', '$timeout', 'ScriptLogger', 'ParlayUtility', PromenadeStandardEndpointCardCommandTabController])
	.directive("promenadeStandardEndpointCardCommands", PromenadeStandardEndpointCardCommands)
	.directive("promenadeStandardEndpointCardCommandContainer", ['RecursionHelper', 'ParlayPersistence', 'ParlayUtility', PromenadeStandardEndpointCardCommandContainer]);