var parlay_endpoint = angular.module('parlay.endpoints.endpoint', ['ngMaterial', 'ngMessages', 'ngMdIcons', 'templates-main', 'parlay.store']);

parlay_endpoint.factory('ParlayEndpoint', function () {
    
    function NotImplementedError(method) {
        console.warn(method + ' is not implemented for ' + this.name);
    }
    
    function ParlayEndpoint(data, protocol) {
        
        Object.defineProperty(this, 'name', {
            value: data.NAME,
            enumerable: true,
            writeable: false,
            configurable: false
        });
        
        Object.defineProperty(this, 'protocol', {
            value: protocol,
            writeable: false,
            enumerable: false,
            configurable: false
        });
        
        this.type = 'ParlayEndpoint';
        
        this.interfaces = data.INTERFACES;
        
        this.directives = {
            toolbar: [],
            tabs: []
        };
        
    }
    
    ParlayEndpoint.prototype.getType = function () {
        return this.type;
    };
    
    ParlayEndpoint.prototype.getDirectives = function () {
        return [this.directives];
    };
    
    ParlayEndpoint.prototype.matchesQuery = function (query) {
        NotImplementedError('matchesQuery');
    };
    
    return ParlayEndpoint;
    
});

parlay_endpoint.directive('parlayEndpointCard', ['$compile', 'ParlayStore', function ($compile, ParlayStore) {
    return {
        templateUrl: '../parlay_components/endpoints/directives/parlay-endpoint-card.html',
        link: function (scope, element, attributes) {
	        
            scope.endpoint = scope.container.ref;
            
            var key = 'parlayEndpointCard.' + scope.container.ref.name.replace(' ', '_') + '_' + scope.container.uid;
            
            // Converts directive names to snake-case which Angular requires during directive compilation.
            function snake_case(name) {
                return name.replace(/[A-Z]/g, function(letter, pos) {
                    return (pos ? '-' : '') + letter.toLowerCase();
                });
            }
            
            function compileToolbar() {
	            // Locate toolbar where we are going to insert dynamic directives.
	            var toolbar = element[0].querySelector('div.md-toolbar-tools');
	            
	            scope.endpoint.getDirectives().filter(function (endpoint) {
	                return endpoint.hasOwnProperty('toolbar');
	            }).reduce(function (previous, endpoint) {
	                return previous.concat(endpoint.toolbar.map(function (directive) {
	                    return '<' + snake_case(directive, '-') + ' endpoint="endpoint" layout-fill layout="row" layout-align="space-between center"></' + snake_case(directive, '-') + '>';    
	                }));
	            }, []).forEach(function (directive_string) {
					toolbar.insertBefore($compile(directive_string)(scope)[0], toolbar.firstChild);
	            });
            }
            
            function compileTabs() {
	            // Locate tabs where we are going to insert dynamic directives.
	            var tabs = element[0].querySelector('md-tabs');
	            
	            // Append tabs directives.
	            scope.endpoint.getDirectives().filter(function (endpoint) {
	                return endpoint.hasOwnProperty('tabs');
	            }).reduce(function (previous, endpoint) {
		            return previous.concat(endpoint.tabs.map(function (directive) {
	                    return '<' + snake_case(directive, '-') + ' endpoint="endpoint"></' + snake_case(directive, '-') + '>';
	                }));
	            }, []).forEach(function (directive_string) {
	                tabs.appendChild($compile(directive_string)(scope)[0]);
	            });
            }
            
            function setActiveTab(newValue, oldValue) {
	            var saved_state = ParlayStore('endpoints').getDirectiveContainer('parlayEndpointCard.' + scope.endpoint.name.replace(' ', '_') + '_' + scope.container.uid);
		        if (saved_state) scope.active_tab_index = saved_state.active_tab_index;
	            scope.$watch('active_tab_index', setAttr(key));
            }
            
            function setAttr(directive) {
		        return function () {
			    	ParlayStore('endpoints').set(directive.replace(' ', '_'), this.exp, this.last);    
		        };		        
	        }
	        
	        function removeItem(directive) {
				return function () {
					ParlayStore('endpoints').remove(directive.replace(' ', '_'));
				};
			}
	        
	        compileToolbar();
	        compileTabs();
            setActiveTab();
            
            scope.$watch('$index', setAttr(key));
	        scope.$on('$destroy', removeItem(key));
            
        }
    };
}]);