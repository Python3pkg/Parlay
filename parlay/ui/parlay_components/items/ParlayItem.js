function ParlayItemFactory() {
    
    function ParlayItem(data, protocol) {
        
        Object.defineProperty(this, "name", {
            value: data.NAME,
            enumerable: true,
            writeable: false,
            configurable: false
        });
        
        Object.defineProperty(this, "protocol", {
            value: protocol,
            writeable: false,
            enumerable: false,
            configurable: false
        });
        
        this.type = "ParlayItem";
        
        this.interfaces = data.INTERFACES;
        
        this.directives = {
            toolbar: {
	            default: [],
	            available: []
            },
            tabs: {
	            default: [],
	            available: []
            },
            available_cache: {}
        };
        
    }

    /**
     * Gets item type.
     * @returns {String} - Item type
     */
    ParlayItem.prototype.getType = function () {
        return this.type;
    };

    /**
     * Adds the given directives as defaults for the specified target.
     * @param {String} target - Directive target: toolbar or tabs.
     * @param {Array} directives - Array of directive names.
     */
    ParlayItem.prototype.addDefaultDirectives = function(target, directives) {
	    this.directives[target].default = this.directives[target].default.concat(directives);
    };

    /**
     * Adds the given directives as available for the specified target.
     * @param {String} target - Directive target: toolbar or tabs.
     * @param {Array} directives - Array of directive names.
     */
    ParlayItem.prototype.addAvailableDirectives = function (target, directives) {
	    this.directives[target].available = this.directives[target].available.concat(directives);
    };

    /**
     * Gets the directives that have been added as defaults.
     * @returns {Object} - Mapping of target -> Array of default directive names.
     */
    ParlayItem.prototype.getDefaultDirectives = function () {
	    var item = this;
	    return Object.keys(item.directives).reduce(function (accumulator, target) {
		    if (item.directives[target].hasOwnProperty("default")) {
			    accumulator[target] = item.directives[target].default;
		    }
		    return accumulator;
	    }, {});
    };

    /**
     * Gets the directives that have been added as available.
     * @returns {Object} - Mapping of target -> Array of available directive names.
     */
    ParlayItem.prototype.getAvailableDirectives = function () {
        var item = this;
    	item.directives.available_cache = Object.keys(item.directives).filter(function (target) {
			return target.indexOf("cache") === -1;
		}).reduce(function (accumulator, target) {
	        accumulator[target] = item.directives[target].available;
	        return accumulator;
        }, {});
        return this.directives.available_cache;
    };

    /**
     * Abstract method that should be overwritten by those that prototypically inherit from ParlayItem.
     * @param {String} query - Given query that would be matched to the item's properties.
     */
    ParlayItem.prototype.matchesQuery = function (query) {
	    console.warn("matchesQuery is not implemented for " + this.name);
    };
    
    return ParlayItem;
    
}

/**
 * @name ParlayItemCard
 * @param $compile - AngularJS service used to create a template function used to link scope and template.
 * @param ParlayPersistence - Parlay service used to persist scope values into local storage.
 * @description
 * Directive that displays data from a particular ParlayItem.
 * @returns AngularJS directive
 */
function ParlayItemCard($compile, ParlayPersistence) {
    return {
        templateUrl: "../parlay_components/items/directives/parlay-item-card.html",
        link: function (scope, element, attributes) {

	        // Grab the item reference from the container for convenience of using scope.item.
	        scope.item = scope.container.ref;

            var directive_name = "parlayItemCard." + scope.item.name.replace(" ", "_") + "_" + scope.container.uid;

            // Container used to hold the names of the currently active directives.
            // Allows for ParlayPersistence to mirror the active directives to local storage.
	        scope.active_directives = {};

            /**
             * Compiles then stores the given directive in active_directives.
             * @param {String} target - Directive location: toolbar or tabs.
             * @param {String} directive - Name of directive
             */
	        scope.activateDirective = function (target, directive) {
				if (target === "tabs") compileTabs([directive]);
				else if (target === "toolbar") compileToolbar([directive]);

				if (!scope.active_directives.hasOwnProperty(target)) {
					scope.active_directives[target] = [];
				}
				scope.active_directives[target].push(directive);
			};

            /**
             * Removes the directive from the active_directives container.
             * @param {String} target - Directive location: toolbar or tabs.
             * @param {String} directive - Name of directive
             */
			scope.deactivateDirective = function (target, directive) {
                var index = scope.active_directives[target].indexOf(directive);
                // If directive exists in active_directives then remove, otherwise throw error.
                if (index > -1) scope.active_directives[target].splice(index, 1);
                else throw new Error("Attempted to deactivate inactive directive");
			};

            // Using ParlayPersistence we will first attempt to restore these values then we will record them to ParlayStore.
            ParlayPersistence(directive_name, "$index", scope);
            ParlayPersistence(directive_name, "active_tab_index", scope);
            ParlayPersistence(directive_name, "active_directives", scope);

            /**
	         * Compiles the toolbar set on the item.
	         * @param {Array} directives - Array of directive name strings.
	         */
            function compileToolbar(directives) {
	            // Locate toolbar where we are going to insert dynamic directives.
	            var toolbar = element[0].querySelector("div.md-toolbar-tools");

	            directives.map(function (directive) {
                    return "<" + directive.snakeCase() + " item='item' layout-fill layout='row' layout-align='space-between center'></" + directive.snakeCase() + ">";
                }).forEach(function (directive_string) {
					toolbar.insertBefore($compile(directive_string)(scope)[0], toolbar.firstChild);
	            });
            }

            /**
	         * Compiles the tabs set on the item.
		     * @param {Array} directives - Array of directive name strings.
	         */
            function compileTabs(directives) {
	            // Locate tabs where we are going to insert dynamic directives.
	            var tabs = element[0].querySelector("md-tabs");

	            directives.map(function (directive) {
					return "<" + directive.snakeCase() + " item='item'></" + directive.snakeCase() + ">";
	            }).forEach(function (directive_string) {
	                tabs.appendChild($compile(directive_string)(scope)[0]);
	            });
            }

            /**
             * Activate each default directive.
             */
            function defaultDirectives() {
	            var defaults = scope.item.getDefaultDirectives();
	            Object.keys(defaults).forEach(function (target) {
		            defaults[target].forEach(function (directive) {
			            scope.activateDirective(target, directive);
		            });
	            });
            }

            /**
             * Compile each directive in active_directives.
             */
            function restoreDirectives() {
	            Object.keys(scope.active_directives).forEach(function (target) {
		         	if (target === "tabs") compileTabs(scope.active_directives[target]);
		         	else if (target === "toolbar") compileToolbar(scope.active_directives[target]);
	         	});
            }

            // Wait for ParlayPersistence to restore active_directives on scope.
            var one_time = scope.$watch("active_directives", function (newValue) {
                // Call the deregistration function for this one time listener.
                one_time();
                // If active_directives has been restored by ParlayPersistence then we should restore these directives.
                // Otherwise we should use the defaults.
                if (newValue !== undefined && Object.keys(newValue).length > 0) restoreDirectives();
	            else defaultDirectives();
            });

        }
    };
}

angular.module("parlay.items.item", ["ngMaterial", "ngMessages", "ngMdIcons", "templates-main", "parlay.store.persistence", "parlay.utility", "parlay.items.widgettab"])
	.factory("ParlayItem", ParlayItemFactory)
	.directive("parlayItemCard", ["$compile", "ParlayPersistence", ParlayItemCard]);