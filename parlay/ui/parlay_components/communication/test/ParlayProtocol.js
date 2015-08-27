(function () {
    'use strict';

	var sample_endpoints = function () {
	    var endpoints = [];
	    
	    for (var i = 0; i < 50; i++) {
	        endpoints.push({
	            ID: 100 + i,
	            INTERFACES: [],
	            NAME: 'TEST' + i,
	            TEMPLATE: 'STD_ENDPOINT'
	        });
	    }
	    
	    return endpoints;
	}();
	
	var sample_discovery = {
	    CHILDREN: sample_endpoints,
	    NAME: 'TestProtocol',
	    TEMPLATE: 'Protocol'
	};

    describe('parlay.protocols.protocol', function() {
    
        beforeEach(module('parlay.protocols.protocol'));
        beforeEach(module('mock.parlay.socket'));
        
        describe('ParlayProtocol', function () {
            var rootScope, protocol;
            
            beforeEach(inject(function($rootScope, _ParlayProtocol_) {
                /*jshint newcap: false */
                rootScope = $rootScope;
                protocol = new _ParlayProtocol_({name: 'TestProtocol', protocol_type: 'TestProtocolType'});
            }));
            
            it('constructs', function() {
                expect(protocol.getName()).toBe('TestProtocol');
                expect(protocol.getType()).toBe('TestProtocolType');
            });
            
            it('accesses attributes', function () {
                expect(protocol.getAvailableEndpoints()).toEqual([]);
                expect(protocol.getLog()).toEqual([]);
            });
            
            it('records message', function () {
                expect(protocol.getLog()).toEqual([]);
                protocol.recordLog({type: 'test'});
                expect(protocol.getLog()).toEqual([{type: 'test'}]);
            });
            
            it('invokes onMessage callback', function () {
                var testMsg = 'hey';
                protocol.onMessage(function (testResp) {
                    expect(testResp).toEqual(testMsg);
                });
                protocol.invokeCallbacks(testMsg);
            });
            
            describe('performs operations onClose', function () {
                
                it('has listener', function () {
	                protocol.onOpen();
                    expect(protocol.hasListener()).toBeTruthy();
                    protocol.onClose();
                    expect(protocol.hasListener()).toBeFalsy();
                });
                
            });
            
            describe('sends a message', function () {
                
                it('resolves', function (done) {
                    protocol.sendMessage({type: 'test'}, {data:[]}, {type: 'test'}).then(function (response) {
                        expect(response).toEqual({STATUS: 0, data:[]});
                        done();
                    });
                    rootScope.$apply();                    
                });
                
                it('rejects', function(done) {
                    protocol.sendMessage({type: 'test'}, null, {type: 'test'}).catch(function (response) {
                        expect(response).toEqual({STATUS: -1});
                        done();
                    });
                    rootScope.$apply();
                });
            });
            
            describe('adding discovery information', function () {
                
                it('adds endpoints', function () {
                    expect(protocol.getAvailableEndpoints().length).toBe(0);
                    protocol.addEndpoints(sample_discovery.CHILDREN);
                    expect(protocol.getAvailableEndpoints().length).toBe(50);
                });
                
                it('gets field keys', function () {
                    expect(protocol.getDynamicFieldKeys().length).toBe(0);
                });
                
                it('buildsFields', function () {
                    expect(protocol.getDynamicFieldKeys().length).toBe(0);
                    protocol.buildFields(sample_discovery);
                    expect(protocol.getDynamicFieldKeys().length).toBe(3);
                });
                
                it('does full discovery process', function () {
                    expect(protocol.getAvailableEndpoints().length).toBe(0);
                    expect(protocol.getDynamicFieldKeys().length).toBe(0);
                    protocol.addDiscoveryInfo(sample_discovery);
                    expect(protocol.NAME).toBe(sample_discovery.NAME);
                    expect(protocol.getAvailableEndpoints().length).toBe(50);
                    expect(protocol.getDynamicFieldKeys().length).toBe(3);
                });
                
            });

        });
        
    });
    
}());